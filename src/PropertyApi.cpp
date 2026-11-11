#include "PropertyApi.hpp"

#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/FField.hpp>
#include <Unreal/FWeakObjectPtr.hpp>
#include <Unreal/SoftObjectPtr.hpp>
#include <Unreal/NameTypes.hpp>
#include <Unreal/Property/FEnumProperty.hpp>
#include <Unreal/Property/FStrProperty.hpp>
#include <Unreal/Property/FTextProperty.hpp>
#include <Unreal/UClass.hpp>
#include <Unreal/UEnum.hpp>
#include <Unreal/UFunction.hpp>
#include <Unreal/UObject.hpp>
#include <Unreal/UScriptStruct.hpp>
#include <Unreal/UStruct.hpp>

#include <pyue/identifier.hpp>

#include <atomic>
#include <unordered_map>
#include <cstring>
#include <functional>
#include <memory>
#include <new>

#include <pyue/errors.hpp>

#include "GameThreadExecutor.hpp"
#include "SafeCall.hpp"
#include "ObjectApi.hpp"
#include "UnrealBridge.hpp"

using namespace RC;

namespace pyue::objects::properties
{
    namespace
    {
        // Every struct buffer is 16-byte aligned, which covers every engine struct (their alignment is at most 16).
        constexpr std::align_val_t struct_alignment{16};

        std::atomic<std::uint64_t> s_created{0};
        std::atomic<std::uint64_t> s_destroyed{0};
        std::atomic<std::uint64_t> s_queued{0};
        std::atomic<std::uint64_t> s_leaked_inner{0};
        std::atomic<std::uint64_t> s_leaked_block{0};
        std::atomic<std::uint64_t> s_faults_survived{0};

        // Destroys the contents of a struct buffer with the engine and frees the block. If the struct type is
        // already gone the inner allocations are leaked rather than risking a crash.
        auto destroy_buffer(std::uint8_t* data, const ObjectRef& structure) -> void
        {
            bool destroyed_inner = false;
            try
            {
                if (objects::is_valid(structure))
                {
                    static_cast<Unreal::UStruct*>(resolve(structure))->DestroyStruct(data);
                    destroyed_inner = true;
                }
            }
            catch (...)
            {
                // Leak the inner allocations; never let an exception escape a destructor.
            }
            if (!destroyed_inner) ++s_leaked_inner;
            ::operator delete(data, struct_alignment);
            ++s_destroyed;
        }
    }

    struct OwnedStruct
    {
        std::uint8_t* data{nullptr};
        ObjectRef structure{};
        bool owns_memory{true};          // false for borrowed memory (hook parameters): never freed here
        std::atomic<bool> alive{true};   // borrowed memory is marked dead when its scope ends
        std::vector<std::pair<const void*, std::uint8_t*>> out_addresses; // borrowed hook parameters: where each out parameter really is

        OwnedStruct() = default;
        OwnedStruct(const OwnedStruct&) = delete;
        auto operator=(const OwnedStruct&) -> OwnedStruct& = delete;

        // May run on any thread (a Python object dies wherever the GIL holder dropped its last reference),
        // but DestroyStruct is an engine call, so off the game thread it is queued there instead.
        ~OwnedStruct()
        {
            if (!data || !owns_memory) return;
            std::uint8_t* buffer = data;
            const ObjectRef structure_ref = structure;
            if (!game_thread_executor::game_thread_known() || game_thread_executor::method() == game_thread_executor::Method::None)
            {
                ++s_leaked_block;
                return; // no safe place to call the engine: leak this one block
            }
            if (game_thread_executor::on_game_thread())
            {
                destroy_buffer(buffer, structure_ref);
            }
            else
            {
                ++s_queued;
                game_thread_executor::post([buffer, structure_ref] { destroy_buffer(buffer, structure_ref); });
            }
        }
    };

    namespace
    {
        enum class PointerKind
        {
            Strong,    // UObject*
            Weak,      // FWeakObjectPtr
            Lazy,      // FLazyObjectPtr: the weak part is the in-memory pointer (its GUID cannot be resolved or created from here)
            Interface, // FScriptInterface (read only)
        };

        struct Located
        {
            Unreal::FProperty* property{nullptr};
            std::uint8_t* base{nullptr}; // the container (owner object or struct value) the property lives in
            void* data{nullptr};
            std::uint32_t offset_in_owner{0};
            PropertyInfo info{};
            PropertyInfo element_info{}; // FixedArray only: the type of one element
            std::uint32_t fixed_dim{0};  // FixedArray only: ArrayDim
            BoolLayout bool_layout{};
            PointerKind pointer{PointerKind::Strong}; // how an Object category value is stored
            std::uint8_t* override_address{nullptr}; // set when `data` is a redirected out parameter of hook parameters
        };

        auto narrow(const StringType& text) -> std::string
        {
            std::string result;
            result.reserve(text.size());
            for (const auto c : text) result.push_back(c < 128 ? static_cast<char>(c) : '?');
            return result;
        }

        // Scalar kind of a numeric property, or nullopt if it is not one we support.
        auto scalar_kind_of(Unreal::FProperty* property) -> std::optional<ScalarKind>
        {
            if (Unreal::CastField<Unreal::FIntProperty>(property)) return ScalarKind::Int32;
            if (Unreal::CastField<Unreal::FFloatProperty>(property)) return ScalarKind::Float;
            if (Unreal::CastField<Unreal::FDoubleProperty>(property)) return ScalarKind::Double;
            if (Unreal::CastField<Unreal::FByteProperty>(property)) return ScalarKind::UInt8;
            if (Unreal::CastField<Unreal::FInt64Property>(property)) return ScalarKind::Int64;
            if (Unreal::CastField<Unreal::FInt16Property>(property)) return ScalarKind::Int16;
            if (Unreal::CastField<Unreal::FInt8Property>(property)) return ScalarKind::Int8;
            if (Unreal::CastField<Unreal::FUInt16Property>(property)) return ScalarKind::UInt16;
            if (Unreal::CastField<Unreal::FUInt32Property>(property)) return ScalarKind::UInt32;
            if (Unreal::CastField<Unreal::FUInt64Property>(property)) return ScalarKind::UInt64;
            return std::nullopt;
        }

        // The UStruct (as a UObject) whose fields the target's properties are looked up in, and the memory
        // they live in. GetPropertyByNameInChain treats a UStruct receiver as the struct itself, so for an
        // instance the receiver must be its CLASS, never the instance (or the lookup would use the wrong layout
        // when the instance is itself a class or struct object).
        struct Container
        {
            Unreal::UObject* struct_object{nullptr};
            std::uint8_t* base{nullptr};
        };

        // Borrowed memory (hook parameters) is only valid while the hook runs.
        auto check_alive(const Target& target) -> void
        {
            if (target.is_owned() && !target.owned->alive.load(std::memory_order_acquire))
            {
                throw InvalidObjectError("the hook parameters are only valid inside the hook callback; use copy() to keep a value");
            }
        }

        auto container_of(const Target& target) -> Container
        {
            game_thread_executor::require_game_thread();
            check_alive(target);
            if (target.is_owned()) return Container{resolve(target.structure), target.base_override ? target.base_override : target.owned->data + target.offset};
            Unreal::UObject* owner = resolve(target.owner);
            if (!target.is_struct()) return Container{owner->GetClassPrivate(), reinterpret_cast<std::uint8_t*>(owner)};
            return Container{resolve(target.structure), reinterpret_cast<std::uint8_t*>(owner) + target.offset};
        }

        // "/Script/Engine.ENetRole" for an enum object, empty for null.
        auto enum_path_of(Unreal::UEnum* enumeration) -> std::wstring
        {
            if (!enumeration) return {};
            const std::wstring full(enumeration->GetFullName()); // "Enum /Script/Engine.ENetRole"
            const auto space = full.find(L' ');
            return space == std::wstring::npos ? full : full.substr(space + 1);
        }

        // Fills located.info (and the bool layout) from located.property.
        auto classify(Located& located) -> void
        {
            Unreal::FProperty* property = located.property;
            if (auto* as_bool = Unreal::CastField<Unreal::FBoolProperty>(property))
            {
                located.info.category = Category::Scalar;
                located.info.scalar = ScalarKind::Bool;
                located.bool_layout = BoolLayout{as_bool->GetFieldMask(), as_bool->GetByteMask(), as_bool->GetByteOffset()};
            }
            else if (auto kind = scalar_kind_of(property))
            {
                located.info.category = Category::Scalar;
                located.info.scalar = *kind;
                if (auto* as_byte = Unreal::CastField<Unreal::FByteProperty>(property)) located.info.enum_path = enum_path_of(as_byte->GetEnum());
            }
            else if (auto* as_enum = Unreal::CastField<Unreal::FEnumProperty>(property))
            {
                // An enum is stored as its underlying integer type.
                auto enum_kind = as_enum->GetUnderlyingProp() ? scalar_kind_of(as_enum->GetUnderlyingProp()) : std::nullopt;
                if (enum_kind)
                {
                    located.info.category = Category::Scalar;
                    located.info.scalar = *enum_kind;
                    located.info.enum_path = enum_path_of(as_enum->GetEnum());
                }
            }
            else if (Unreal::CastField<Unreal::FNameProperty>(property))
            {
                located.info.category = Category::Name;
            }
            else if (Unreal::CastField<Unreal::FStrProperty>(property))
            {
                located.info.category = Category::String;
            }
            else if (Unreal::CastField<Unreal::FTextProperty>(property))
            {
                located.info.category = Category::Text;
            }
            else if (Unreal::CastField<Unreal::FObjectProperty>(property))
            {
                located.info.category = Category::Object;
            }
            else if (Unreal::CastField<Unreal::FWeakObjectProperty>(property))
            {
                located.info.category = Category::Object;
                located.pointer = PointerKind::Weak;
            }
            else if (Unreal::CastField<Unreal::FLazyObjectProperty>(property))
            {
                located.info.category = Category::Object;
                located.pointer = PointerKind::Lazy;
            }
            else if (Unreal::CastField<Unreal::FInterfaceProperty>(property))
            {
                located.info.category = Category::Object;
                located.pointer = PointerKind::Interface;
            }
            else if (Unreal::CastField<Unreal::FSoftObjectProperty>(property)) // also soft class properties
            {
                located.info.category = Category::SoftPath;
            }
            else if (Unreal::CastField<Unreal::FMulticastInlineDelegateProperty>(property))
            {
                located.info.category = Category::MulticastDelegate;
            }
            else if (Unreal::CastField<Unreal::FDelegateProperty>(property))
            {
                located.info.category = Category::Delegate;
            }
            else if (Unreal::CastField<Unreal::FStructProperty>(property))
            {
                located.info.category = Category::Struct;
            }
            else if (Unreal::CastField<Unreal::FArrayProperty>(property))
            {
                located.info.category = Category::Array;
            }
            else if (Unreal::CastField<Unreal::FMapProperty>(property))
            {
                located.info.category = Category::Map;
            }
            else if (Unreal::CastField<Unreal::FSetProperty>(property))
            {
                located.info.category = Category::Set;
            }
        }

        // A name that is no property of the struct may be the identifier form of one ("Sun_height" for "Sun height"): the form the generated
        // typings declare for engine names that are not Python identifiers. Accepted when exactly one property has that form (several would be
        // ambiguous and stay unresolved). Answers are cached per struct: a method call probes the property list first, with a name that is
        // usually no property at all.
        struct AliasKey
        {
            const void* structure;
            std::wstring name;
            auto operator==(const AliasKey& other) const -> bool { return structure == other.structure && name == other.name; }
        };
        struct AliasKeyHash
        {
            auto operator()(const AliasKey& key) const -> std::size_t { return std::hash<const void*>()(key.structure) ^ (std::hash<std::wstring>()(key.name) * 31); }
        };
        struct AliasEntry
        {
            Unreal::FProperty* property;
            const void* first_child; // stamps the entry: a struct that is freed and reused at the same address has other children
            std::int32_t properties_size;
        };

        auto find_property_by_alias(Unreal::UStruct* structure, bool parameters_only, std::wstring_view requested) -> Unreal::FProperty*
        {
            if (requested.find(L'_') == std::wstring_view::npos) return nullptr;      // an alias always contains a replaced character
            static std::unordered_map<AliasKey, AliasEntry, AliasKeyHash> cache;
            const void* first_child = structure->GetChildProperties();
            const AliasKey key{structure, std::wstring(requested)};
            const auto cached = cache.find(key);
            if (cached != cache.end() && cached->second.first_child == first_child && cached->second.properties_size == structure->GetPropertiesSize()) return cached->second.property;

            Unreal::FProperty* found = nullptr;
            std::wstring found_name;
            int matches = 0;
            const auto flags = parameters_only ? Unreal::EFieldIterationFlags::None : Unreal::EFieldIterationFlags::IncludeSuper;
            for (Unreal::FProperty* candidate : Unreal::TFieldRange<Unreal::FProperty>(structure, flags))
            {
                if (parameters_only && !candidate->HasAnyPropertyFlags(Unreal::CPF_Parm)) continue;
                const std::wstring engine_name(candidate->GetName());
                if (pyue::is_identifier_alias(requested, std::wstring_view(engine_name)) && engine_name != found_name)
                {
                    found = candidate;
                    found_name = engine_name;
                    ++matches;
                }
            }
            if (matches != 1) found = nullptr;
            if (cache.size() > 50000) cache.clear();
            cache[key] = AliasEntry{found, first_child, structure->GetPropertiesSize()};
            return found;
        }

        // Finds the property and its value pointer. Throws if a handle is dead or off the game thread.
        auto locate(const Target& target, std::wstring_view name) -> std::optional<Located>
        {
            const Container container = container_of(target);

            // Look the name up without adding it to the engine's name table: a name that does not exist cannot be a property.
            const auto name_id = bridge::name_from_string(name, false);
            const bool is_function_block = container.struct_object->IsA<Unreal::UFunction>();
            Unreal::FProperty* property = nullptr;
            if (name_id)
            {
                const Unreal::FName wanted(name_id->index, name_id->number);
                if (is_function_block)
                {
                    // A function-shaped container (a parameter block, hook parameters) exposes the function's OWN PARAMETERS only.
                    // A Blueprint function also has local variables as properties, but their storage lies beyond the parameter
                    // block, and an overriding function must not inherit its parent's layout: so no locals and no super chain.
                    for (Unreal::FProperty* candidate : Unreal::TFieldRange<Unreal::FProperty>(static_cast<Unreal::UStruct*>(container.struct_object), Unreal::EFieldIterationFlags::None))
                    {
                        if (candidate->HasAnyPropertyFlags(Unreal::CPF_Parm) && candidate->GetFName().Equals(wanted))
                        {
                            property = candidate;
                            break;
                        }
                    }
                }
                else
                {
                    property = container.struct_object->GetPropertyByNameInChain(wanted);
                }
            }
            if (!property) property = find_property_by_alias(static_cast<Unreal::UStruct*>(container.struct_object), is_function_block, name);
            if (!property) return std::nullopt;

            Located located;
            located.property = property;
            located.base = container.base;
            located.info.type_name = narrow(property->GetClass().GetName());

            located.offset_in_owner = target.offset + static_cast<std::uint32_t>(property->GetOffset_Internal());
            located.data = container.base + property->GetOffset_Internal();
            if (target.is_owned() && !target.base_override && !target.owned->out_addresses.empty())
            {
                // Hook parameters: an out parameter lives in the caller's memory (FFrame::OutParms), not in our copy.
                for (const auto& [known, address] : target.owned->out_addresses)
                {
                    if (known == static_cast<const void*>(property))
                    {
                        located.data = address;
                        located.override_address = address;
                        break;
                    }
                }
            }

            classify(located);
            if (property->GetArrayDim() > 1)
            {
                // classify() described one element; the property as a whole is a FixedArray of them.
                located.fixed_dim = static_cast<std::uint32_t>(property->GetArrayDim());
                located.element_info = located.info;
                located.info.category = Category::FixedArray;
            }
            return located;
        }

        // Where a struct value's bytes are (owned buffer, or inside the owner object). Resolves the handles.
        auto struct_data(const Target& target) -> const std::uint8_t*
        {
            check_alive(target);
            if (target.base_override) return target.base_override;
            if (target.is_owned()) return target.owned->data + target.offset;
            return reinterpret_cast<const std::uint8_t*>(resolve(target.owner)) + target.offset;
        }

        auto require(const std::optional<Located>& located, std::wstring_view name) -> const Located&
        {
            if (!located) throw PropertyNotFoundError("the object has no property named '" + narrow(StringType(name)) + "'");
            if (located->info.category == Category::Unsupported)
            {
                throw UEError("property '" + narrow(StringType(name)) + "' has type " + located->info.type_name + ", which is not supported yet");
            }
            return *located;
        }
    }

    namespace
    {
        // FText values are read and written through the engine's own text library, called like any other function: the
        // FText property copies its value into the library function's parameter block (and back out of its result), so
        // reference counting is done by the engine's code and never by a hand-made FText.
        auto text_library_call(const wchar_t* function_path, const std::function<void(const Target& block)>& fill, const std::function<void(const Target& block)>& use) -> void
        {
            const ObjectRef function = api::find_by_path(function_path);
            const ObjectRef library = api::find_by_path(L"/Script/Engine.Default__KismetTextLibrary");
            if (function.handle.index < 0 || library.handle.index < 0) throw UEError("the engine's text library is not available");
            Target block = make_param_block(function);
            fill(block);
            call_function(library, function, block);
            use(block);
        }

        auto text_to_string(Unreal::FProperty* text_property, void* text_data) -> std::wstring
        {
            std::wstring result;
            text_library_call(
                    L"/Script/Engine.KismetTextLibrary:Conv_TextToString",
                    [&](const Target& block) {
                        const auto in_text = locate(block, L"InText");
                        if (!in_text) throw UEError("Conv_TextToString has no InText parameter");
                        text_property->CopyCompleteValue(in_text->data, text_data);
                    },
                    [&](const Target& block) { result = get(block, L"ReturnValue").text; });
            return result;
        }

        auto string_to_text(Unreal::FProperty* text_property, void* text_data, const std::wstring& text) -> void
        {
            text_library_call(
                    L"/Script/Engine.KismetTextLibrary:Conv_StringToText",
                    [&](const Target& block) {
                        Value in;
                        in.category = Category::String;
                        in.text = text;
                        set(block, L"InString", in);
                    },
                    [&](const Target& block) {
                        const auto result = locate(block, L"ReturnValue");
                        if (!result) throw UEError("Conv_StringToText has no ReturnValue");
                        text_property->CopyCompleteValue(text_data, result->data);
                    });
        }

        auto read_located(const Located& found, const Target& target) -> Value
        {
            Value value;
            value.category = found.info.category;
            switch (found.info.category)
            {
            case Category::Scalar:
                value.scalar = read_scalar(found.info.scalar, found.data, found.bool_layout);
                value.enum_path = found.info.enum_path;
                break;
            case Category::Name: {
                const auto& fname = *static_cast<const Unreal::FName*>(found.data);
                value.name = NameId{static_cast<std::uint32_t>(fname.GetComparisonIndex().ToUnstableInt()), static_cast<std::int32_t>(fname.GetNumber())};
                break;
            }
            case Category::String: {
                const auto& string = *static_cast<const Unreal::FString*>(found.data);
                value.text.assign(*string, static_cast<size_t>(string.Len()));
                break;
            }
            case Category::Object: {
                Unreal::UObject* object = nullptr;
                switch (found.pointer)
                {
                case PointerKind::Strong: object = *static_cast<Unreal::UObject**>(found.data); break;
                case PointerKind::Weak: object = static_cast<Unreal::FWeakObjectPtr*>(found.data)->Get(); break; // null once the object is gone
                case PointerKind::Lazy: object = static_cast<Unreal::FLazyObjectPtr*>(found.data)->WeakPtr.Get(); break; // see PointerKind::Lazy
                case PointerKind::Interface: object = *static_cast<Unreal::UObject**>(found.data); break;       // FScriptInterface: object pointer first
                }
                // The stored pointer can be stale or garbage (a raw pointer the game never cleared): validate before using it.
                value.object = acquire_untrusted(object); // null or not a live object -> null ref -> None
                break;
            }
            case Category::Text:
                value.text = text_to_string(found.property, found.data);
                break;
            case Category::SoftPath: {
                const Unreal::FString path = static_cast<Unreal::FSoftObjectPtr*>(found.data)->ToString();
                value.text.assign(*path, static_cast<size_t>(path.Len()));
                break;
            }
            case Category::Delegate: {
                const auto& delegate = *static_cast<Unreal::FScriptDelegate*>(found.data);
                if (Unreal::UObject* bound = delegate.GetUObject())
                {
                    const Unreal::FName function = delegate.GetFunctionName();
                    value.bindings.push_back(Binding{acquire(bound), NameId{static_cast<std::uint32_t>(function.GetComparisonIndex().ToUnstableInt()), static_cast<std::int32_t>(function.GetNumber())}});
                }
                break;
            }
            case Category::MulticastDelegate: {
                const auto& delegates = static_cast<Unreal::FMulticastScriptDelegate*>(found.data)->InvocationList;
                for (int32_t i = 0; i < delegates.Num(); ++i)
                {
                    if (Unreal::UObject* bound = delegates[i].GetUObject())
                    {
                        const Unreal::FName function = delegates[i].GetFunctionName();
                        value.bindings.push_back(Binding{acquire(bound), NameId{static_cast<std::uint32_t>(function.GetComparisonIndex().ToUnstableInt()), static_cast<std::int32_t>(function.GetNumber())}});
                    }
                }
                break;
            }
            case Category::Struct: {
                auto* struct_property = Unreal::CastField<Unreal::FStructProperty>(found.property);
                Unreal::UScriptStruct* script_struct = struct_property ? struct_property->GetStruct() : nullptr;
                if (!script_struct) throw UEError("the struct property has no struct type");
                value.structure = Target{target.owner, acquire(script_struct), found.offset_in_owner, target.owned};
                // A struct inside redirected memory (an out parameter, or something in one) is addressed directly.
                if (target.base_override || found.override_address) value.structure.base_override = static_cast<std::uint8_t*>(found.data);
                break;
            }
            case Category::Array:
            case Category::FixedArray:
            case Category::Map:
            case Category::Set:
            case Category::Unsupported:
                break;
            }
            return value;
        }

        auto write_located(const Located& found, const Value& value, std::wstring_view name) -> void
        {
            if (value.category != found.info.category) throw UEError("the value does not match the type of property '" + narrow(StringType(name)) + "'");

            switch (found.info.category)
            {
            case Category::Scalar: {
                const ScalarKind kind = found.info.scalar;
                switch (value.scalar.type)
                {
                case ScalarValue::Type::Bool:
                    if (kind != ScalarKind::Bool) throw UEError("a bool was given for a non-bool property");
                    write_bool(found.data, found.bool_layout, value.scalar.boolean);
                    break;
                case ScalarValue::Type::Signed:
                    if (!signed_fits(kind, value.scalar.signed_value)) throw UEError("the integer does not fit the property");
                    write_signed(kind, found.data, value.scalar.signed_value);
                    break;
                case ScalarValue::Type::Unsigned:
                    if (!unsigned_fits(kind, value.scalar.unsigned_value)) throw UEError("the integer does not fit the property");
                    write_unsigned(kind, found.data, value.scalar.unsigned_value);
                    break;
                case ScalarValue::Type::Float:
                    if (!float_fits(kind, value.scalar.float_value)) throw UEError("the number does not fit the property");
                    write_float(kind, found.data, value.scalar.float_value);
                    break;
                }
                break;
            }
            case Category::Name:
                *static_cast<Unreal::FName*>(found.data) = Unreal::FName(static_cast<uint32_t>(value.name.index), static_cast<uint32_t>(value.name.number));
                break;
            case Category::String:
                *static_cast<Unreal::FString*>(found.data) = Unreal::FString(StringType(value.text));
                break;
            case Category::Object: {
                if (found.pointer == PointerKind::Interface) throw UEError("interface properties are read-only");
                // None clears the reference; anything else must be an instance of the property's class.
                Unreal::UObject* new_value = value.object.handle.index < 0 ? nullptr : resolve(value.object);
                if (new_value)
                {
                    auto* object_property = Unreal::CastField<Unreal::FObjectPropertyBase>(found.property);
                    if (Unreal::UClass* property_class = object_property ? object_property->GetPropertyClass() : nullptr)
                    {
                        if (!new_value->IsA(property_class))
                        {
                            throw UEError("the object is not an instance of the property's class " + narrow(property_class->GetName()));
                        }
                    }
                    if (auto* class_property = Unreal::CastField<Unreal::FClassProperty>(found.property))
                    {
                        Unreal::UClass* meta_class = class_property->GetMetaClass();
                        if (!new_value->IsA<Unreal::UClass>() || (meta_class && !static_cast<Unreal::UClass*>(new_value)->IsChildOf(meta_class)))
                        {
                            throw UEError("the class does not derive from the property's meta class" + (meta_class ? " " + narrow(meta_class->GetName()) : std::string()));
                        }
                    }
                }
                if (found.pointer == PointerKind::Weak)
                {
                    auto* weak = static_cast<Unreal::FWeakObjectPtr*>(found.data);
                    if (new_value) *weak = new_value;
                    else weak->Reset();
                }
                else if (found.pointer == PointerKind::Lazy)
                {
                    // The old GUID must go: the engine would resolve it again once the weak part is stale. A new GUID cannot
                    // be created from here, so the pointer is valid in memory only (it is not saved or replicated by GUID).
                    auto* lazy = static_cast<Unreal::FLazyObjectPtr*>(found.data);
                    lazy->Reset();
                    if (new_value) lazy->WeakPtr = new_value;
                }
                else
                {
                    *static_cast<Unreal::UObject**>(found.data) = new_value;
                }
                break;
            }
            case Category::Text:
                string_to_text(found.property, found.data, value.text);
                break;
            case Category::SoftPath: {
                auto* soft = static_cast<Unreal::FSoftObjectPtr*>(found.data);
                if (value.text.empty()) *soft = Unreal::FSoftObjectPtr();
                else *soft = Unreal::FSoftObjectPtr(Unreal::FSoftObjectPath(Unreal::FString(StringType(value.text))));
                break;
            }
            case Category::Delegate:
            case Category::MulticastDelegate:
                throw UEError("delegate properties are read-only");
            case Category::Struct:
                throw UEError("assign struct properties through copy_struct()");
            case Category::Array:
            case Category::FixedArray:
            case Category::Map:
            case Category::Set:
                throw UEError("containers are assigned through their own functions");
            case Category::Unsupported:
                break;
            }
        }
    }

    auto describe(const Target& target, std::wstring_view name) -> std::optional<PropertyInfo>
    {
        const auto located = locate(target, name);
        if (!located) return std::nullopt;
        return located->info;
    }

    auto get(const Target& target, std::wstring_view name) -> Value
    {
        const auto located = locate(target, name);
        const Located& found = require(located, name);
        return read_located(found, target);
    }

    auto set(const Target& target, std::wstring_view name, const Value& value) -> void
    {
        const auto located = locate(target, name);
        const Located& found = require(located, name);
        write_located(found, value, name);
    }

    auto copy_struct(const Target& dest, std::wstring_view name, const Target& source) -> void
    {
        const auto located = locate(dest, name);
        const Located& found = require(located, name);
        if (found.info.category != Category::Struct) throw UEError("property '" + narrow(StringType(name)) + "' is not a struct");

        auto* struct_property = Unreal::CastField<Unreal::FStructProperty>(found.property);
        Unreal::UObject* dest_struct = struct_property ? struct_property->GetStruct() : nullptr;

        // The source must be the same struct type, and live.
        game_thread_executor::require_game_thread();
        if (!source.is_struct() || resolve(source.structure) != dest_struct) throw UEError("the source is not a struct of the same type as the property");

        found.property->CopyCompleteValue(found.data, struct_data(source));
    }

    auto property_names(const Target& target) -> std::vector<std::wstring>
    {
        const Container container = container_of(target);
        std::vector<std::wstring> names;
        auto* structure = static_cast<Unreal::UStruct*>(container.struct_object);
        const bool parameters_only = container.struct_object->IsA<Unreal::UFunction>(); // see locate(): own parameters only
        const auto flags = parameters_only ? Unreal::EFieldIterationFlags::None : Unreal::EFieldIterationFlags::IncludeSuper;
        for (Unreal::FProperty* property : Unreal::TFieldRange<Unreal::FProperty>(structure, flags))
        {
            if (parameters_only && !property->HasAnyPropertyFlags(Unreal::CPF_Parm)) continue;
            names.push_back(std::wstring(property->GetName()));
        }
        return names;
    }

    auto is_valid(const Target& target) -> bool
    {
        if (target.is_owned()) return target.owned->alive.load(std::memory_order_acquire) && objects::is_valid(target.structure);
        return objects::is_valid(target.owner) && (!target.is_struct() || objects::is_valid(target.structure));
    }

    auto owns_memory(const Target& target) -> bool
    {
        return target.is_owned() && target.owned->owns_memory;
    }

    auto is_scoped(const Target& target) -> bool
    {
        return target.is_owned() && !target.owned->owns_memory;
    }

    ScopedParams::ScopedParams(const ObjectRef& function, void* locals)
    {
        auto borrowed = std::make_shared<OwnedStruct>();
        borrowed->data = static_cast<std::uint8_t*>(locals);
        borrowed->structure = function;
        borrowed->owns_memory = false;
        m_target = Target{ObjectRef{}, function, 0, borrowed};
    }

    auto ScopedParams::add_out_parameter(const void* property, std::uint8_t* address) -> void
    {
        if (m_target.owned && property && address) m_target.owned->out_addresses.emplace_back(property, address);
    }

    ScopedParams::~ScopedParams()
    {
        if (m_target.owned) m_target.owned->alive.store(false, std::memory_order_release);
    }

    auto faults_survived() -> std::uint64_t
    {
        return s_faults_survived.load();
    }

    auto owned_struct_stats() -> OwnedStructStats
    {
        return OwnedStructStats{s_created.load(), s_destroyed.load(), s_queued.load(), s_leaked_inner.load(), s_leaked_block.load()};
    }

    namespace
    {
        // Allocates a zeroed block of `size` bytes, initialises it as `type` (a script struct or a function) and
        // wraps it in an owned Target.
        auto new_owned(const ObjectRef& structure, Unreal::UStruct* type, std::size_t size) -> Target
        {
            auto* data = static_cast<std::uint8_t*>(::operator new(size, struct_alignment));
            std::memset(data, 0, size);
            try
            {
                type->InitializeStruct(data);
            }
            catch (...)
            {
                ::operator delete(data, struct_alignment);
                throw;
            }

            auto owned = std::make_shared<OwnedStruct>();
            owned->data = data;
            owned->structure = structure;
            ++s_created;
            return Target{ObjectRef{}, structure, 0, owned};
        }
    }

    auto make_param_block(const ObjectRef& function) -> Target
    {
        game_thread_executor::require_game_thread();
        Unreal::UObject* object = resolve(function);
        if (!object->IsA<Unreal::UFunction>()) throw UEError("this object is not a function");
        auto* type = static_cast<Unreal::UFunction*>(object);

        // InitializeStruct/DestroyStruct touch EVERY property of the function, and a Blueprint function's local variables
        // come after its parameters, so the block needs the full properties size, not just ParmsSize. (Allocating
        // ParmsSize overflowed the heap for Blueprint functions: STATUS_HEAP_CORRUPTION.) Only the parameters are exposed.
        const std::int32_t properties_size = type->GetPropertiesSize();
        const std::int32_t parms_size = type->GetParmsSize();
        const std::int32_t wanted = properties_size > parms_size ? properties_size : parms_size;
        const std::size_t size = wanted > 0 ? static_cast<std::size_t>(wanted) : 1;
        return new_owned(function, type, size);
    }

    auto call_function(const ObjectRef& context, const ObjectRef& function, const Target& block) -> void
    {
        game_thread_executor::require_game_thread();
        Unreal::UObject* context_object = resolve(context);
        Unreal::UObject* function_object = resolve(function);
        if (!function_object->IsA<Unreal::UFunction>()) throw UEError("this object is not a function");
        auto* function_pointer = static_cast<Unreal::UFunction*>(function_object);

        if (!block.is_owned() || !block.owned->owns_memory || resolve(block.structure) != function_object)
        {
            throw UEError("the parameter block does not belong to this function");
        }

        // An instance function may only run on an object of its class; a static function runs on any object.
        if ((function_pointer->GetFunctionFlags() & static_cast<std::uint32_t>(Unreal::FUNC_Static)) == 0)
        {
            Unreal::UObject* owner = function_pointer->GetOuterPrivate();
            // An interface function belongs to the interface class, which the implementing object's class does not derive from:
            // accept it when the object's own class resolves that name to this very function (that lookup includes the
            // implemented interfaces).
            bool through_interface = false;
            if (owner && owner->IsA<Unreal::UClass>() && !context_object->IsA(static_cast<Unreal::UClass*>(owner)) &&
                (static_cast<Unreal::UClass*>(owner)->GetClassFlags() & Unreal::CLASS_Interface) != 0)
            {
                const ObjectRef found = api::find_function(api::class_of(context), std::wstring(function_pointer->GetName()));
                through_interface = found.handle.index >= 0 && resolve(found) == function_object;
            }
            if (owner && owner->IsA<Unreal::UClass>() && !through_interface && !context_object->IsA(static_cast<Unreal::UClass*>(owner)))
            {
                throw UEError("'" + narrow(function_pointer->GetName()) + "' belongs to class " + narrow(owner->GetName()) + ", which this object is not an instance of");
            }
        }

        // A hardware fault inside game code (a missing context, a stale pointer in an argument) becomes a UEError instead of
        // ending the process. Best effort: see SafeCall.hpp.
        struct Call
        {
            Unreal::UObject* context;
            Unreal::UFunction* function;
            void* parameters;
        } call{context_object, function_pointer, block.owned->data};
        FaultInfo fault;
        const bool completed = call_guarded(
                [](void* argument) {
                    auto* c = static_cast<Call*>(argument);
                    c->context->ProcessEvent(c->function, c->parameters);
                },
                &call, fault);
        if (!completed)
        {
            ++s_faults_survived;
            throw UEError("the game faulted (" + std::string(describe_fault(fault)) + ") while running '" + narrow(function_pointer->GetName()) +
                          "'; the call was abandoned, but the game may be in an inconsistent state");
        }
    }

    auto make_struct(const ObjectRef& script_struct) -> Target
    {
        game_thread_executor::require_game_thread();
        Unreal::UObject* object = resolve(script_struct);
        if (!object->IsA<Unreal::UScriptStruct>()) throw UEError("this object is not a struct type");
        auto* type = static_cast<Unreal::UScriptStruct*>(object);

        const std::size_t size = type->GetStructureSize() > 0 ? static_cast<std::size_t>(type->GetStructureSize()) : 1;
        return new_owned(script_struct, type, size);
    }

    auto duplicate_struct(const Target& source) -> Target
    {
        game_thread_executor::require_game_thread();
        if (!source.is_struct()) throw UEError("not a struct");
        Unreal::UObject* type_object = resolve(source.structure);
        const std::uint8_t* from = struct_data(source); // also refuses borrowed memory that is out of scope

        if (type_object->IsA<Unreal::UScriptStruct>())
        {
            Target copy = make_struct(source.structure);
            static_cast<Unreal::UScriptStruct*>(type_object)->CopyScriptStruct(copy.owned->data, from);
            return copy;
        }
        if (type_object->IsA<Unreal::UFunction>())
        {
            // A parameter block (made by make_param_block, or borrowed from a hook). Only the parameters are copied:
            // a Blueprint function's locals live after them and are not part of the block.
            Target copy = make_param_block(source.structure);
            auto* function = static_cast<Unreal::UFunction*>(type_object);
            for (Unreal::FProperty* property : Unreal::TFieldRange<Unreal::FProperty>(function, Unreal::EFieldIterationFlags::None))
            {
                if (property->HasAnyPropertyFlags(Unreal::CPF_Parm)) property->CopyCompleteValue_InContainer(copy.owned->data, from);
            }
            return copy;
        }
        throw UEError("not a struct");
    }

    auto struct_name(const Target& target) -> std::wstring
    {
        if (!target.is_struct()) throw UEError("not a struct");
        return bridge::name_to_string(objects::api::fname(target.structure));
    }

    namespace
    {
        struct ArrayAccess
        {
            Unreal::FArrayProperty* property{nullptr};
            void* data{nullptr}; // the FScriptArray
            Located element{};   // the element property; element.data is filled in per index
        };

        auto locate_array(const Target& target, std::wstring_view name) -> ArrayAccess
        {
            const auto located = locate(target, name);
            const Located& found = require(located, name);
            if (found.info.category != Category::Array) throw UEError("property '" + narrow(StringType(name)) + "' is not an array (it has type " + found.info.type_name + ")");

            ArrayAccess access;
            access.property = Unreal::CastField<Unreal::FArrayProperty>(found.property);
            access.data = found.data;
            Unreal::FProperty* inner = access.property ? access.property->GetInner() : nullptr;
            if (!inner) throw UEError("the array property has no element type");
            access.element.property = inner;
            access.element.info.type_name = narrow(inner->GetClass().GetName());
            classify(access.element);
            return access;
        }

        auto element_at(ArrayAccess& access, std::int64_t index, std::wstring_view name) -> Located
        {
            Unreal::FScriptArrayHelper helper(access.property, access.data);
            if (index < 0 || index >= helper.Num()) throw UEError("array index out of range for '" + narrow(StringType(name)) + "'");
            Located element = access.element;
            element.data = helper.GetRawPtr(static_cast<int32_t>(index));
            return element;
        }

        auto require_supported_element(const ArrayAccess& access, std::wstring_view name) -> void
        {
            if (access.element.info.category == Category::Unsupported || access.element.info.category == Category::Array || access.element.info.category == Category::Delegate ||
                access.element.info.category == Category::MulticastDelegate)
            {
                throw UEError("array '" + narrow(StringType(name)) + "' holds " + access.element.info.type_name + ", which is not supported yet");
            }
        }
    }

    namespace
    {
        auto locate_fixed(const Target& target, std::wstring_view name) -> Located
        {
            const auto located = locate(target, name);
            const Located& found = require(located, name);
            if (found.info.category != Category::FixedArray) throw UEError("property '" + narrow(StringType(name)) + "' is not a fixed-size array (it has type " + found.info.type_name + ")");
            if (found.element_info.category == Category::Unsupported || found.element_info.category == Category::Array || found.element_info.category == Category::Map ||
                found.element_info.category == Category::Set || found.element_info.category == Category::Delegate || found.element_info.category == Category::MulticastDelegate)
            {
                throw UEError("fixed-size array '" + narrow(StringType(name)) + "' holds " + found.element_info.type_name + ", which is not supported yet");
            }
            return found;
        }

        auto fixed_element(const Located& found, std::int64_t index, std::wstring_view name) -> Located
        {
            if (index < 0 || index >= static_cast<std::int64_t>(found.fixed_dim)) throw UEError("array index out of range for '" + narrow(StringType(name)) + "'");
            const std::uint32_t step = static_cast<std::uint32_t>(index) * static_cast<std::uint32_t>(found.property->GetElementSize());
            Located element = found;
            element.info = found.element_info;
            element.fixed_dim = 0;
            element.data = static_cast<std::uint8_t*>(found.data) + step;
            element.offset_in_owner = found.offset_in_owner + step;
            if (found.override_address) element.override_address = found.override_address + step;
            return element;
        }
    }

    auto fixed_info(const Target& target, std::wstring_view name) -> ArrayInfo
    {
        const auto located = locate(target, name);
        const Located& found = require(located, name);
        if (found.info.category != Category::FixedArray) throw UEError("property '" + narrow(StringType(name)) + "' is not a fixed-size array (it has type " + found.info.type_name + ")");
        ArrayInfo info;
        info.length = found.fixed_dim;
        info.element = found.element_info;
        return info;
    }

    auto fixed_get(const Target& target, std::wstring_view name, std::int64_t index) -> Value
    {
        const Located found = locate_fixed(target, name);
        return read_located(fixed_element(found, index, name), target);
    }

    auto array_info(const Target& target, std::wstring_view name) -> ArrayInfo
    {
        ArrayAccess access = locate_array(target, name);
        Unreal::FScriptArrayHelper helper(access.property, access.data);
        ArrayInfo info;
        info.length = static_cast<std::size_t>(helper.Num());
        info.element = access.element.info;
        return info;
    }

    auto array_get(const Target& target, std::wstring_view name, std::int64_t index) -> Value
    {
        ArrayAccess access = locate_array(target, name);
        require_supported_element(access, name);
        const Located element = element_at(access, index, name);
        if (element.info.category != Category::Struct) return read_located(element, target);

        // A struct element is returned as a copy: its address changes whenever the array reallocates, so a view into it
        // could dangle.
        auto* struct_property = Unreal::CastField<Unreal::FStructProperty>(element.property);
        Unreal::UScriptStruct* script_struct = struct_property ? struct_property->GetStruct() : nullptr;
        if (!script_struct) throw UEError("the struct element has no struct type");
        Target copy = make_struct(acquire(script_struct));
        script_struct->CopyScriptStruct(copy.owned->data, element.data);
        Value value;
        value.category = Category::Struct;
        value.structure = copy;
        return value;
    }

    namespace
    {
        // Writes `value` into one element (struct elements are copied in; they must be the element's struct type).
        auto write_element(const Located& element, const Value& value, std::wstring_view name) -> void
        {
            if (value.category != element.info.category) throw UEError("the value does not match the element type of array '" + narrow(StringType(name)) + "'");
            if (element.info.category == Category::Struct)
            {
                auto* struct_property = Unreal::CastField<Unreal::FStructProperty>(element.property);
                Unreal::UScriptStruct* script_struct = struct_property ? struct_property->GetStruct() : nullptr;
                if (!script_struct || !value.structure.is_struct() || resolve(value.structure.structure) != script_struct)
                {
                    throw UEError("the source is not a struct of the same type as the array's elements");
                }
                script_struct->CopyScriptStruct(element.data, struct_data(value.structure));
                return;
            }
            write_located(element, value, name);
        }

        // A standalone, initialised element buffer of the array's element type; destroyed with the engine on scope exit.
        class ScratchElement
        {
        public:
            explicit ScratchElement(Unreal::FProperty* inner)
                : m_property(inner), m_size(static_cast<std::size_t>(inner->GetElementSize()) > 0 ? static_cast<std::size_t>(inner->GetElementSize()) : 1)
            {
                m_data = static_cast<std::uint8_t*>(::operator new(m_size, struct_alignment));
                std::memset(m_data, 0, m_size);
                try
                {
                    inner->InitializeValue(m_data);
                }
                catch (...)
                {
                    ::operator delete(m_data, struct_alignment);
                    throw;
                }
            }
            ~ScratchElement()
            {
                try
                {
                    m_property->DestroyValue(m_data);
                }
                catch (...)
                {
                }
                ::operator delete(m_data, struct_alignment);
            }
            ScratchElement(const ScratchElement&) = delete;
            auto operator=(const ScratchElement&) -> ScratchElement& = delete;
            auto data() const -> std::uint8_t* { return m_data; }

        private:
            Unreal::FProperty* m_property;
            std::size_t m_size;
            std::uint8_t* m_data{nullptr};
        };
    }

    auto array_set(const Target& target, std::wstring_view name, std::int64_t index, const Value& value) -> void
    {
        ArrayAccess access = locate_array(target, name);
        require_supported_element(access, name);
        write_element(element_at(access, index, name), value, name);
    }

    auto array_replace(const Target& target, std::wstring_view name, std::int64_t start, std::int64_t stop, const std::vector<Value>& values) -> void
    {
        ArrayAccess access = locate_array(target, name);
        require_supported_element(access, name);
        Unreal::FScriptArrayHelper helper(access.property, access.data);
        const std::int64_t length = helper.Num();
        if (start < 0 || stop < start || stop > length) throw UEError("array range out of bounds for '" + narrow(StringType(name)) + "'");
        if (values.size() > static_cast<std::size_t>(INT32_MAX - length)) throw UEError("the array would become too large");

        // Phase 1 (can fail, array untouched): convert every new value into a scratch element, so a bad value is
        // reported before anything changes.
        std::vector<std::unique_ptr<ScratchElement>> scratch;
        scratch.reserve(values.size());
        for (const Value& value : values)
        {
            auto element = std::make_unique<ScratchElement>(access.element.property);
            Located target_element = access.element;
            target_element.data = element->data();
            write_element(target_element, value, name);
            scratch.push_back(std::move(element));
        }

        // Phase 2 (engine allocation only): cut out the old range, insert blank elements, copy the new values in.
        if (stop > start) helper.RemoveValues(static_cast<int32_t>(start), static_cast<int32_t>(stop - start));
        if (!scratch.empty())
        {
            helper.InsertValues(static_cast<int32_t>(start), static_cast<int32_t>(scratch.size()));
            for (std::size_t i = 0; i < scratch.size(); ++i)
            {
                access.element.property->CopyCompleteValue(helper.GetRawPtr(static_cast<int32_t>(start + static_cast<std::int64_t>(i))), scratch[i]->data());
            }
        }
    }

    namespace
    {
        // Reads an element value; a struct comes back as an owned copy (the container may reallocate, so no views).
        auto read_copy(const Located& element, const Target& target) -> Value
        {
            if (element.info.category != Category::Struct) return read_located(element, target);
            auto* struct_property = Unreal::CastField<Unreal::FStructProperty>(element.property);
            Unreal::UScriptStruct* script_struct = struct_property ? struct_property->GetStruct() : nullptr;
            if (!script_struct) throw UEError("the struct element has no struct type");
            Target copy = make_struct(acquire(script_struct));
            script_struct->CopyScriptStruct(copy.owned->data, element.data);
            Value value;
            value.category = Category::Struct;
            value.structure = copy;
            return value;
        }

        auto proto_of(Unreal::FProperty* property) -> Located
        {
            Located located;
            located.property = property;
            located.info.type_name = narrow(property->GetClass().GetName());
            classify(located);
            return located;
        }

        auto require_element_type(const Located& proto, std::wstring_view name, const char* role) -> void
        {
            if (proto.info.category == Category::Unsupported || proto.info.category == Category::Array || proto.info.category == Category::Map ||
                proto.info.category == Category::Set || proto.info.category == Category::Delegate || proto.info.category == Category::MulticastDelegate)
            {
                throw UEError("'" + narrow(StringType(name)) + "' has " + role + " of type " + proto.info.type_name + ", which is not supported yet");
            }
        }

        // Hashing needs the engine's type hash for the key; structs have none we can rely on.
        auto require_lookup_key(const Located& key, std::wstring_view name) -> void
        {
            if (key.info.category == Category::Struct) throw UEError("'" + narrow(StringType(name)) + "' has struct keys; looking up or adding by struct key is not supported yet");
        }

        auto hasher(Unreal::FProperty* key) { return [key](const void* element) -> Unreal::uint32 { return key->GetValueTypeHash(element); }; }
        auto equality(Unreal::FProperty* key) { return [key](const void* a, const void* b) -> bool { return key->Identical(a, b); }; }

        // A scratch copy of `value` in the element type: validates the value before any container is touched.
        auto make_scratch(const Located& proto, const Value& value, std::wstring_view name) -> std::unique_ptr<ScratchElement>
        {
            auto scratch = std::make_unique<ScratchElement>(proto.property);
            Located located = proto;
            located.data = scratch->data();
            write_element(located, value, name);
            return scratch;
        }

        struct MapAccess
        {
            Unreal::FMapProperty* property{nullptr};
            Unreal::FScriptMap* map{nullptr};
            Located key{};
            Located value{};
            Unreal::FScriptMapLayout layout{};
        };

        auto locate_map(const Target& target, std::wstring_view name) -> MapAccess
        {
            const auto located = locate(target, name);
            const Located& found = require(located, name);
            if (found.info.category != Category::Map) throw UEError("property '" + narrow(StringType(name)) + "' is not a map (it has type " + found.info.type_name + ")");
            MapAccess access;
            access.property = Unreal::CastField<Unreal::FMapProperty>(found.property);
            access.map = static_cast<Unreal::FScriptMap*>(found.data);
            Unreal::FProperty* key = access.property ? access.property->GetKeyProp() : nullptr;
            Unreal::FProperty* value = access.property ? access.property->GetValueProp() : nullptr;
            if (!key || !value) throw UEError("the map property has no key or value type");
            access.key = proto_of(key);
            access.value = proto_of(value);
            access.layout = Unreal::FScriptMap::GetScriptLayout(key->GetSize(), key->GetMinAlignment(), value->GetSize(), value->GetMinAlignment());
            return access;
        }

        auto pair_key(const MapAccess& access, std::int32_t index) -> std::uint8_t*
        {
            return static_cast<std::uint8_t*>(access.map->GetData(index, access.layout));
        }

        auto find_pair(MapAccess& access, const void* key_data) -> std::int32_t
        {
            return access.map->FindPairIndex(key_data, access.layout, hasher(access.key.property), equality(access.key.property));
        }

        auto destroy_pair(MapAccess& access, std::uint8_t* pair) -> void
        {
            access.value.property->DestroyValue(pair + access.layout.ValueOffset);
            access.key.property->DestroyValue(pair);
        }

        struct SetAccess
        {
            Unreal::FSetProperty* property{nullptr};
            Unreal::FScriptSet* set{nullptr};
            Located element{};
            Unreal::FScriptSetLayout layout{};
        };

        auto locate_set(const Target& target, std::wstring_view name) -> SetAccess
        {
            const auto located = locate(target, name);
            const Located& found = require(located, name);
            if (found.info.category != Category::Set) throw UEError("property '" + narrow(StringType(name)) + "' is not a set (it has type " + found.info.type_name + ")");
            SetAccess access;
            access.property = Unreal::CastField<Unreal::FSetProperty>(found.property);
            access.set = static_cast<Unreal::FScriptSet*>(found.data);
            Unreal::FProperty* element = access.property ? access.property->GetElementProp() : nullptr;
            if (!element) throw UEError("the set property has no element type");
            access.element = proto_of(element);
            access.layout = Unreal::FScriptSet::GetScriptLayout(element->GetSize(), element->GetMinAlignment());
            return access;
        }
    }

    auto map_info(const Target& target, std::wstring_view name) -> MapInfo
    {
        MapAccess access = locate_map(target, name);
        return MapInfo{static_cast<std::size_t>(access.map->Num()), access.key.info, access.value.info};
    }

    auto map_entries(const Target& target, std::wstring_view name) -> std::vector<MapEntry>
    {
        MapAccess access = locate_map(target, name);
        require_element_type(access.key, name, "keys");
        require_element_type(access.value, name, "values");
        std::vector<MapEntry> entries;
        const std::int32_t max_index = access.map->GetMaxIndex();
        for (std::int32_t i = 0; i < max_index; ++i)
        {
            if (!access.map->IsValidIndex(i)) continue;
            std::uint8_t* pair = pair_key(access, i);
            Located key = access.key;
            key.data = pair;
            Located value = access.value;
            value.data = pair + access.layout.ValueOffset;
            entries.push_back(MapEntry{read_copy(key, target), read_copy(value, target)});
        }
        return entries;
    }

    auto map_find(const Target& target, std::wstring_view name, const Value& key) -> std::optional<Value>
    {
        MapAccess access = locate_map(target, name);
        require_element_type(access.key, name, "keys");
        require_element_type(access.value, name, "values");
        require_lookup_key(access.key, name);
        auto scratch = make_scratch(access.key, key, name);
        const std::int32_t index = find_pair(access, scratch->data());
        if (index == Unreal::INDEX_NONE) return std::nullopt;
        Located value = access.value;
        value.data = pair_key(access, index) + access.layout.ValueOffset;
        return read_copy(value, target);
    }

    auto map_update(const Target& target, std::wstring_view name, const std::vector<MapEntry>& entries, bool replace_all) -> void
    {
        MapAccess access = locate_map(target, name);
        require_element_type(access.key, name, "keys");
        require_element_type(access.value, name, "values");
        require_lookup_key(access.key, name);

        // Phase 1: convert everything; nothing is touched if a key or value is bad.
        std::vector<std::pair<std::unique_ptr<ScratchElement>, std::unique_ptr<ScratchElement>>> scratch;
        scratch.reserve(entries.size());
        for (const MapEntry& entry : entries)
        {
            scratch.emplace_back(make_scratch(access.key, entry.key, name), make_scratch(access.value, entry.value, name));
        }

        // Phase 2: engine work only.
        if (replace_all)
        {
            const std::int32_t max_index = access.map->GetMaxIndex();
            for (std::int32_t i = 0; i < max_index; ++i)
            {
                if (access.map->IsValidIndex(i)) destroy_pair(access, pair_key(access, i));
            }
            access.map->Empty(0, access.layout);
        }
        Unreal::FProperty* key_property = access.key.property;
        Unreal::FProperty* value_property = access.value.property;
        for (auto& [key, value] : scratch)
        {
            const std::uint8_t* key_data = key->data();
            const std::uint8_t* value_data = value->data();
            access.map->Add(key_data, value_data, access.layout, hasher(key_property), equality(key_property),
                            [&](void* new_key) {
                                key_property->InitializeValue(new_key);
                                key_property->CopyCompleteValue(new_key, key_data);
                            },
                            [&](void* new_value) {
                                value_property->InitializeValue(new_value);
                                value_property->CopyCompleteValue(new_value, value_data);
                            },
                            [&](void* existing_value) { value_property->CopyCompleteValue(existing_value, value_data); },
                            [&](void* old_key) { key_property->DestroyValue(old_key); },
                            [&](void* old_value) { value_property->DestroyValue(old_value); });
        }
    }

    auto map_remove(const Target& target, std::wstring_view name, const Value& key) -> bool
    {
        MapAccess access = locate_map(target, name);
        require_element_type(access.key, name, "keys");
        require_lookup_key(access.key, name);
        auto scratch = make_scratch(access.key, key, name);
        const std::int32_t index = find_pair(access, scratch->data());
        if (index == Unreal::INDEX_NONE) return false;
        destroy_pair(access, pair_key(access, index));
        access.map->RemoveAt(index, access.layout);
        return true;
    }

    auto set_info(const Target& target, std::wstring_view name) -> SetInfo
    {
        SetAccess access = locate_set(target, name);
        return SetInfo{static_cast<std::size_t>(access.set->Num()), access.element.info};
    }

    auto set_values(const Target& target, std::wstring_view name) -> std::vector<Value>
    {
        SetAccess access = locate_set(target, name);
        require_element_type(access.element, name, "elements");
        std::vector<Value> values;
        const std::int32_t max_index = access.set->GetMaxIndex();
        for (std::int32_t i = 0; i < max_index; ++i)
        {
            if (!access.set->IsValidIndex(i)) continue;
            Located element = access.element;
            element.data = static_cast<std::uint8_t*>(access.set->GetData(i, access.layout));
            values.push_back(read_copy(element, target));
        }
        return values;
    }

    auto set_contains(const Target& target, std::wstring_view name, const Value& value) -> bool
    {
        SetAccess access = locate_set(target, name);
        require_element_type(access.element, name, "elements");
        require_lookup_key(access.element, name);
        auto scratch = make_scratch(access.element, value, name);
        return access.set->FindIndex(scratch->data(), access.layout, hasher(access.element.property), equality(access.element.property)) != Unreal::INDEX_NONE;
    }

    auto set_update(const Target& target, std::wstring_view name, const std::vector<Value>& values, bool replace_all) -> void
    {
        SetAccess access = locate_set(target, name);
        require_element_type(access.element, name, "elements");
        require_lookup_key(access.element, name);

        std::vector<std::unique_ptr<ScratchElement>> scratch;
        scratch.reserve(values.size());
        for (const Value& value : values) scratch.push_back(make_scratch(access.element, value, name));

        Unreal::FProperty* property = access.element.property;
        if (replace_all)
        {
            const std::int32_t max_index = access.set->GetMaxIndex();
            for (std::int32_t i = 0; i < max_index; ++i)
            {
                if (access.set->IsValidIndex(i)) property->DestroyValue(access.set->GetData(i, access.layout));
            }
            access.set->Empty(0, access.layout);
        }
        for (auto& element : scratch)
        {
            const std::uint8_t* data = element->data();
            access.set->Add(data, access.layout, hasher(property), equality(property),
                            [&](void* new_element) {
                                property->InitializeValue(new_element);
                                property->CopyCompleteValue(new_element, data);
                            },
                            [&](void* old_element) { property->DestroyValue(old_element); });
        }
    }

    auto set_remove(const Target& target, std::wstring_view name, const Value& value) -> bool
    {
        SetAccess access = locate_set(target, name);
        require_element_type(access.element, name, "elements");
        require_lookup_key(access.element, name);
        auto scratch = make_scratch(access.element, value, name);
        const std::int32_t index = access.set->FindIndex(scratch->data(), access.layout, hasher(access.element.property), equality(access.element.property));
        if (index == Unreal::INDEX_NONE) return false;
        access.element.property->DestroyValue(access.set->GetData(index, access.layout));
        access.set->RemoveAt(index, access.layout);
        return true;
    }

    auto poison_object_pointer(const Target& target, std::wstring_view name, int mode) -> void
    {
        const auto located = locate(target, name);
        const Located& found = require(located, name);
        if (found.info.category != Category::Object || found.pointer != PointerKind::Strong) throw UEError("not a plain object pointer property");

        std::uintptr_t bad = 0;
        switch (mode)
        {
        case 0: { // freed heap memory: still mapped, contents are junk
            auto* block = static_cast<std::uint8_t*>(::operator new(256));
            std::memset(block, 0xDD, 256);
            ::operator delete(block);
            bad = reinterpret_cast<std::uintptr_t>(block);
            break;
        }
        case 1: // an address nothing is mapped at
            bad = 0x00007FF0DEAD0000ull;
            break;
        case 2: { // the inside of a live object
            const Unreal::UObject* live = Unreal::UObjectGlobals::StaticFindObject<Unreal::UObject*>(nullptr, nullptr, STR("/Script/CoreUObject.Object"));
            bad = reinterpret_cast<std::uintptr_t>(live) + 0x10;
            break;
        }
        case 3: // misaligned
            bad = 0x1234567;
            break;
        case 4: // a small integer
            bad = 5;
            break;
        default:
            throw UEError("unknown poison mode");
        }
        *static_cast<std::uintptr_t*>(found.data) = bad;
    }

    auto fixed_set(const Target& target, std::wstring_view name, std::int64_t index, const Value& value) -> void
    {
        const Located found = locate_fixed(target, name);
        write_element(fixed_element(found, index, name), value, name);
    }
}
