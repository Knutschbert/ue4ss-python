#include "ObjectApi.hpp"

#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/Property/FEnumProperty.hpp>
#include <Unreal/UClass.hpp>
#include <Unreal/UEnum.hpp>
#include <Unreal/UFunction.hpp>
#include <Unreal/UObject.hpp>
#include <Unreal/UObjectGlobals.hpp>
#include <Unreal/UScriptStruct.hpp>
#include <Unreal/UStruct.hpp>

#include <pyue/errors.hpp>
#include <pyue/identifier.hpp>

#include "GameThreadExecutor.hpp"
#include "UnrealBridge.hpp"

using namespace RC;

namespace pyue::objects::api
{
    namespace
    {
        // Returns the live object or throws; also enforces the game thread.
        auto live(const ObjectRef& ref) -> Unreal::UObject*
        {
            game_thread_executor::require_game_thread();
            return resolve(ref);
        }

        auto narrow(const StringType& text) -> std::string
        {
            std::string result;
            for (const auto c : text) result.push_back(c < 128 ? static_cast<char>(c) : '?');
            return result;
        }

        // The object as a UStruct, or throws UEError saying what it is not.
        auto struct_pointer(const ObjectRef& ref) -> Unreal::UStruct*
        {
            Unreal::UObject* object = live(ref);
            if (!object->IsA<Unreal::UStruct>()) throw UEError("this object is not a class, struct or function");
            return static_cast<Unreal::UStruct*>(object);
        }

        auto enum_pointer(const ObjectRef& ref) -> Unreal::UEnum*
        {
            Unreal::UObject* object = live(ref);
            if (!object->IsA<Unreal::UEnum>()) throw UEError("this object is not an enum");
            return static_cast<Unreal::UEnum*>(object);
        }

        using UClassPointer = Unreal::UClass*;

        // "EFoo::Bar" -> "Bar"
        auto strip_enum_prefix(std::wstring name) -> std::wstring
        {
            const auto position = name.rfind(L"::");
            return position == std::wstring::npos ? name : name.substr(position + 2);
        }

        auto class_pointer(const ObjectRef& cls) -> Unreal::UClass*
        {
            Unreal::UObject* object = live(cls);
            if (!object->IsA<Unreal::UClass>()) throw UEError("the argument is not a class");
            return static_cast<Unreal::UClass*>(object);
        }
    }

    auto find_by_path(std::wstring_view path) -> ObjectRef
    {
        game_thread_executor::require_game_thread();
        if (!bridge::path_names_exist(path)) return ObjectRef{}; // StaticFindObject would add the unknown names
        return acquire(Unreal::UObjectGlobals::StaticFindObject<Unreal::UObject*>(nullptr, nullptr, StringType(path)));
    }

    auto object_at_address(std::uintptr_t address) -> ObjectRef
    {
        game_thread_executor::require_game_thread();
        return acquire_untrusted(reinterpret_cast<Unreal::UObject*>(address));
    }

    namespace
    {
        constexpr std::int32_t instance_exclusion_flags = 0x10 | 0x20; // RF_ClassDefaultObject | RF_ArchetypeObject
    }

    auto find_first_of(std::wstring_view class_name) -> ObjectRef
    {
        game_thread_executor::require_game_thread();
        // FindFirstOf(text) would add the name to the name table; look it up first and pass the existing FName.
        const auto name_id = bridge::name_from_string(class_name, false);
        if (!name_id) return ObjectRef{};
        const Unreal::FName name(name_id->index, name_id->number);
        if (Unreal::UObject* found = Unreal::UObjectGlobals::FindFirstOf(name)) return acquire(found);
        // FindFirstOf skips classes, functions, enums, structs and packages; asking for such a type by name ('Class',
        // 'BlueprintGeneratedClass', 'Function') needs the unfiltered search.
        std::vector<Unreal::UObject*> meta;
        Unreal::UObjectGlobals::FindObjects(1, name, Unreal::FName(0u, 0u), meta, 0, instance_exclusion_flags, false);
        return meta.empty() ? ObjectRef{} : acquire(meta.front());
    }

    namespace
    {
        // The engine's enumerations include objects that were destroyed but not yet garbage collected (pending kill) or
        // are being collected (unreachable). Like the engine's own "get all actors", leave those out.
        auto acquire_all(const std::vector<Unreal::UObject*>& found) -> std::vector<ObjectRef>
        {
            std::vector<ObjectRef> result;
            result.reserve(found.size());
            for (Unreal::UObject* object : found)
            {
                if (!object) continue;
                const ObjectRef ref = acquire(object);
                if (is_valid(ref)) result.push_back(ref);
            }
            return result;
        }
    }

    auto find_all_of(std::wstring_view class_name) -> std::vector<ObjectRef>
    {
        game_thread_executor::require_game_thread();
        const auto name_id = bridge::name_from_string(class_name, false); // never adds a name
        if (!name_id) return {};
        std::vector<Unreal::UObject*> found;
        const Unreal::FName name(name_id->index, name_id->number);
        Unreal::UObjectGlobals::FindAllOf(name, found);
        if (found.empty())
        {
            // FindAllOf never returns classes, functions, enums, structs or packages, so the instances of such a type
            // ('BlueprintGeneratedClass', 'Class', 'Enum') come back empty. Search without that filter (still no defaults).
            Unreal::UObjectGlobals::FindObjects(0, name, Unreal::FName(0u, 0u), found, 0, instance_exclusion_flags, false);
        }
        return acquire_all(found);
    }

    auto find_objects(std::optional<std::wstring_view> class_name,
                      std::optional<std::wstring_view> object_name,
                      std::int32_t required_flags,
                      std::int32_t banned_flags,
                      bool exact_class,
                      std::size_t limit) -> std::vector<ObjectRef>
    {
        game_thread_executor::require_game_thread();
        if (!class_name && !object_name) throw UEError("find_objects() needs a class name, an object name, or both");

        // FName(0, 0) means "do not filter on this" to the engine.
        Unreal::FName class_filter(0u, 0u);
        Unreal::FName name_filter(0u, 0u);
        if (class_name)
        {
            const auto id = bridge::name_from_string(*class_name, false);
            if (!id) return {};
            class_filter = Unreal::FName(id->index, id->number);
        }
        if (object_name)
        {
            const auto id = bridge::name_from_string(*object_name, false);
            if (!id) return {};
            name_filter = Unreal::FName(id->index, id->number);
        }

        std::vector<Unreal::UObject*> found;
        Unreal::UObjectGlobals::FindObjects(limit, class_filter, name_filter, found, required_flags, banned_flags, exact_class);
        return acquire_all(found);
    }

    auto full_name(const ObjectRef& ref) -> std::wstring
    {
        return std::wstring(live(ref)->GetFullName());
    }

    auto fname(const ObjectRef& ref) -> NameId
    {
        const Unreal::FName name = live(ref)->GetFName();
        return NameId{static_cast<std::uint32_t>(name.GetComparisonIndex().ToUnstableInt()), static_cast<std::int32_t>(name.GetNumber())};
    }

    auto class_of(const ObjectRef& ref) -> ObjectRef
    {
        return acquire(live(ref)->GetClassPrivate());
    }

    auto outer_of(const ObjectRef& ref) -> ObjectRef
    {
        return acquire(live(ref)->GetOuterPrivate());
    }

    auto address(const ObjectRef& ref) -> std::uintptr_t
    {
        return reinterpret_cast<std::uintptr_t>(live(ref));
    }

    auto kind_of(const ObjectRef& ref) -> ObjectKind
    {
        Unreal::UObject* object = live(ref);
        // Most specific first: UClass, UScriptStruct and UFunction all derive from UStruct.
        if (object->IsA<Unreal::UClass>()) return ObjectKind::Class;
        if (object->IsA<Unreal::UScriptStruct>()) return ObjectKind::ScriptStruct;
        if (object->IsA<Unreal::UFunction>()) return ObjectKind::Function;
        if (object->IsA<Unreal::UStruct>()) return ObjectKind::Struct;
        if (object->IsA<Unreal::UEnum>()) return ObjectKind::Enum;
        return ObjectKind::Object;
    }

    auto super_of(const ObjectRef& ref) -> ObjectRef
    {
        return acquire(struct_pointer(ref)->GetSuperStruct());
    }

    auto field_names(const ObjectRef& ref) -> std::vector<std::wstring>
    {
        std::vector<std::wstring> names;
        for (Unreal::FProperty* property : Unreal::TFieldRange<Unreal::FProperty>(struct_pointer(ref), Unreal::EFieldIterationFlags::IncludeSuper))
        {
            names.push_back(std::wstring(property->GetName()));
        }
        return names;
    }

    auto function_names(const ObjectRef& ref) -> std::vector<std::wstring>
    {
        std::vector<std::wstring> names;
        for (Unreal::UFunction* function : Unreal::TFieldRange<Unreal::UFunction>(struct_pointer(ref), Unreal::EFieldIterationFlags::IncludeSuper))
        {
            names.push_back(std::wstring(function->GetName()));
        }
        return names;
    }

    auto find_function(const ObjectRef& ref, std::wstring_view name) -> ObjectRef
    {
        // The receiver is the struct itself (UObject::GetFunctionByNameInChain treats a UStruct receiver as its own field list).
        Unreal::UStruct* structure = struct_pointer(ref);
        const auto name_id = bridge::name_from_string(name, false); // never adds a name
        if (!name_id) return ObjectRef{};
        return acquire(structure->GetFunctionByNameInChain(Unreal::FName(name_id->index, name_id->number)));
    }

    auto find_method(const ObjectRef& object, std::wstring_view name) -> ObjectRef
    {
        const ObjectRef holder = kind_of(object) == ObjectKind::Class ? object : class_of(object);
        const ObjectRef exact = find_function(holder, name);
        if (exact.handle.index >= 0 || name.find(L'_') == std::wstring_view::npos) return exact;
        // The identifier form of a function name with spaces ("Get_Total_Duration_Seconds" for "Get Total Duration Seconds"), the form the typings
        // declare: accepted when exactly one function of the class chain has it.
        Unreal::UFunction* found = nullptr;
        std::wstring found_name;
        int matches = 0;
        for (Unreal::UFunction* function : Unreal::TFieldRange<Unreal::UFunction>(struct_pointer(holder), Unreal::EFieldIterationFlags::IncludeSuper))
        {
            const std::wstring engine_name(function->GetName());
            if (pyue::is_identifier_alias(name, std::wstring_view(engine_name)) && engine_name != found_name)   // an override and its base share a name
            {
                found = function;
                found_name = engine_name;
                ++matches;
            }
        }
        return matches == 1 ? acquire(found) : ObjectRef{};
    }

    auto call_context(const ObjectRef& object, const ObjectRef& function) -> ObjectRef
    {
        if (kind_of(object) != ObjectKind::Class) return object;
        if ((function_flags(function) & static_cast<std::uint32_t>(Unreal::FUNC_Static)) == 0)
        {
            throw UEError("'" + narrow(live(function)->GetName()) + "' is an instance function; call it on an object of the class, not on the class itself");
        }
        return cdo_of(object);
    }

    auto cdo_of(const ObjectRef& ref) -> ObjectRef
    {
        return acquire(class_pointer(ref)->GetClassDefaultObject());
    }

    auto is_child_of(const ObjectRef& ref, const ObjectRef& ancestor) -> bool
    {
        return class_pointer(ref)->IsChildOf(class_pointer(ancestor));
    }

    auto is_child_of_path(const ObjectRef& ref, std::wstring_view ancestor_path) -> bool
    {
        Unreal::UClass* cls = class_pointer(ref);
        UClassPointer ancestor = bridge::path_names_exist(ancestor_path) ? Unreal::UObjectGlobals::StaticFindObject<Unreal::UClass*>(nullptr, nullptr, StringType(ancestor_path)) : nullptr;
        if (!ancestor) throw UEError("no class with that path: " + std::string(ancestor_path.begin(), ancestor_path.end()));
        return cls->IsChildOf(ancestor);
    }

    auto function_flags(const ObjectRef& ref) -> std::uint32_t
    {
        Unreal::UObject* object = live(ref);
        if (!object->IsA<Unreal::UFunction>()) throw UEError("this object is not a function");
        return static_cast<std::uint32_t>(static_cast<Unreal::UFunction*>(object)->GetFunctionFlags());
    }

    auto function_script(const ObjectRef& ref) -> std::vector<std::uint8_t>
    {
        Unreal::UObject* object = live(ref);
        if (!object->IsA<Unreal::UFunction>()) throw UEError("this object is not a function");
        const auto& script = static_cast<Unreal::UFunction*>(object)->GetScript();
        const std::int32_t count = script.Num();
        const std::uint8_t* data = script.GetData();
        if (count < 0 || count > 16 * 1024 * 1024 || (count > 0 && !data)) throw UEError("the function's script is not readable");
        return std::vector<std::uint8_t>(data, data + count);
    }

    namespace
    {
        auto path_of_object(Unreal::UObject* object) -> std::wstring
        {
            if (!object) return {};
            const std::wstring full(object->GetFullName()); // "Class /Script/Engine.Actor"
            const auto space = full.find(L' ');
            return space == std::wstring::npos ? full : full.substr(space + 1);
        }

        auto describe_property(Unreal::FProperty* property) -> TypeInfo
        {
            TypeInfo info;
            info.type_name = narrow(property->GetClass().GetName());
            info.array_dim = property->GetArrayDim();
            if (auto* as_struct = Unreal::CastField<Unreal::FStructProperty>(property))
            {
                Unreal::UScriptStruct* script_struct = as_struct->GetStruct();
                info.struct_path = path_of_object(script_struct);
            }
            else if (auto* as_class = Unreal::CastField<Unreal::FClassProperty>(property))
            {
                info.class_path = path_of_object(as_class->GetMetaClass());
            }
            else if (auto* as_soft_class = Unreal::CastField<Unreal::FSoftClassProperty>(property))
            {
                info.class_path = path_of_object(as_soft_class->GetMetaClass());
            }
            else if (auto* as_object = Unreal::CastField<Unreal::FObjectPropertyBase>(property))
            {
                info.class_path = path_of_object(as_object->GetPropertyClass());
            }
            else if (auto* as_interface = Unreal::CastField<Unreal::FInterfaceProperty>(property))
            {
                info.class_path = path_of_object(as_interface->GetInterfaceClass());
            }
            else if (auto* as_enum = Unreal::CastField<Unreal::FEnumProperty>(property))
            {
                info.enum_path = path_of_object(as_enum->GetEnum());
            }
            else if (auto* as_byte = Unreal::CastField<Unreal::FByteProperty>(property))
            {
                info.enum_path = path_of_object(as_byte->GetEnum());
            }
            else if (auto* as_array = Unreal::CastField<Unreal::FArrayProperty>(property))
            {
                if (as_array->GetInner()) info.inner.push_back(describe_property(as_array->GetInner()));
            }
            else if (auto* as_set = Unreal::CastField<Unreal::FSetProperty>(property))
            {
                if (as_set->GetElementProp()) info.inner.push_back(describe_property(as_set->GetElementProp()));
            }
            else if (auto* as_map = Unreal::CastField<Unreal::FMapProperty>(property))
            {
                if (as_map->GetKeyProp()) info.inner.push_back(describe_property(as_map->GetKeyProp()));
                if (as_map->GetValueProp()) info.inner.push_back(describe_property(as_map->GetValueProp()));
            }
            return info;
        }
    }

    auto field_infos(const ObjectRef& ref, bool include_super) -> std::vector<FieldInfo>
    {
        std::vector<FieldInfo> fields;
        const auto flags = include_super ? Unreal::EFieldIterationFlags::IncludeSuper : Unreal::EFieldIterationFlags::None;
        for (Unreal::FProperty* property : Unreal::TFieldRange<Unreal::FProperty>(struct_pointer(ref), flags))
        {
            FieldInfo info;
            info.name = std::wstring(property->GetName());
            info.type = describe_property(property);
            info.declared_in = path_of_object(property->GetOwner<Unreal::UStruct>());
            info.flags = static_cast<std::uint64_t>(property->GetPropertyFlags());
            fields.push_back(std::move(info));
        }
        return fields;
    }

    auto function_params(const ObjectRef& ref) -> std::vector<ParamInfo>
    {
        Unreal::UObject* object = live(ref);
        if (!object->IsA<Unreal::UFunction>()) throw UEError("this object is not a function");
        auto* function = static_cast<Unreal::UFunction*>(object);

        std::vector<ParamInfo> params;
        for (Unreal::FProperty* property : Unreal::TFieldRange<Unreal::FProperty>(function, Unreal::EFieldIterationFlags::None))
        {
            if (!property->HasAnyPropertyFlags(Unreal::CPF_Parm)) continue;
            ParamInfo info;
            info.name = std::wstring(property->GetName());
            info.type = describe_property(property);
            info.type_name = info.type.type_name;
            info.is_out = property->HasAnyPropertyFlags(Unreal::CPF_OutParm);
            info.is_return = property->HasAnyPropertyFlags(Unreal::CPF_ReturnParm);
            info.is_const = property->HasAnyPropertyFlags(Unreal::CPF_ConstParm);
            info.is_reference = property->HasAnyPropertyFlags(Unreal::CPF_ReferenceParm);
            params.push_back(std::move(info));
        }
        return params;
    }

    auto enum_names(const ObjectRef& ref) -> std::vector<std::pair<std::wstring, std::int64_t>>
    {
        std::vector<std::pair<std::wstring, std::int64_t>> names;
        for (auto&& [name, value] : enum_pointer(ref)->ForEachName())
        {
            names.emplace_back(strip_enum_prefix(std::wstring(name.ToString())), static_cast<std::int64_t>(value));
        }
        return names;
    }

    auto enum_name_by_value(const ObjectRef& ref, std::int64_t value) -> std::optional<std::wstring>
    {
        for (const auto& [name, found] : enum_names(ref))
        {
            if (found == value) return name;
        }
        return std::nullopt;
    }

    auto is_a(const ObjectRef& ref, const ObjectRef& cls) -> bool
    {
        Unreal::UObject* object = live(ref);
        return object->IsA(class_pointer(cls));
    }

    auto is_a_path(const ObjectRef& ref, std::wstring_view class_path) -> bool
    {
        Unreal::UObject* object = live(ref);
        UClassPointer cls = bridge::path_names_exist(class_path) ? Unreal::UObjectGlobals::StaticFindObject<Unreal::UClass*>(nullptr, nullptr, StringType(class_path)) : nullptr;
        if (!cls) throw UEError("no class with that path: " + std::string(class_path.begin(), class_path.end()));
        return object->IsA(cls);
    }
}
