#pragma once

#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include <pyue/name_id.hpp>
#include <pyue/scalar.hpp>

#include "ObjectRegistry.hpp"

// Reading and writing reflected properties of an object or of a struct inside an object.
// Python-free; includes no Unreal headers. Every function requires the game thread and resolves its
// handles first (docs/LIFETIME.md), so a dead owner throws InvalidObjectError instead of crashing.
namespace pyue::objects::properties
{
    // Memory for a struct value that scripts own (not a view into an object). Shared by every Target
    // derived from it, so the memory outlives all views of it. Destroyed with DestroyStruct on the game thread.
    struct OwnedStruct;

    // Where properties are read from. With a null `structure` it is the owner object itself (looked up in
    // the owner's class). Otherwise it is a UScriptStruct value located `offset` bytes into either the owner's
    // memory (a view that stays valid exactly as long as the owner and the struct type do) or, when `owned`
    // is set, into a buffer the script owns (valid as long as the struct type is).
    struct Target
    {
        ObjectRef owner{};
        ObjectRef structure{};
        std::uint32_t offset{0};
        std::shared_ptr<OwnedStruct> owned{};
        // Hook parameters only: when set, this struct view lives at this address instead of owned->data + offset (a struct or
        // array that is an out parameter lives in the caller's memory, not in the hook's copy of the parameters).
        std::uint8_t* base_override{nullptr};

        auto is_struct() const -> bool { return structure.handle.index >= 0; }
        auto is_owned() const -> bool { return static_cast<bool>(owned); }
    };

    enum class Category
    {
        Scalar,      // bool, integers, float, double, enums (as their underlying integer)
        Name,        // FName
        String,      // FString
        Object,      // UObject* (and UClass*)
        Struct,      // a struct stored inline (read as a view)
        Array,       // TArray; read through array_info / array_get / array_set
        Text,        // FText, as its display string (written as a plain, non-localized string)
        SoftPath,    // soft object / soft class reference, as its path string ("" when null)
        Delegate,    // single-cast delegate (read only): zero or one binding
        MulticastDelegate, // multicast delegate (read only): its bindings
        Map,         // TMap; see map_*
        Set,         // TSet; see set_*
        FixedArray,  // a property with ArrayDim > 1 (int Foo[4]); read through fixed_info / fixed_get / fixed_set
        Unsupported, // delegates in containers, ...
    };

    struct PropertyInfo
    {
        Category category{Category::Unsupported};
        ScalarKind scalar{ScalarKind::Int32}; // valid when category == Scalar
        std::string type_name{};              // for messages, e.g. "ArrayProperty"
        std::wstring enum_path{};             // Scalar only: path of the UEnum when the property is an enum (else empty)
    };

    // One delegate binding: a function name on a live object.
    struct Binding
    {
        ObjectRef object{};
        NameId function{};
    };

    struct Value
    {
        Category category{Category::Scalar};
        ScalarValue scalar{};
        NameId name{};
        std::wstring text{};
        ObjectRef object{};  // Category::Object; a null ref means None
        Target structure{};  // Category::Struct: the view
        std::vector<Binding> bindings{}; // Category::Delegate / MulticastDelegate
        std::wstring enum_path{};        // Category::Scalar: path of the UEnum when the value is an enum member (else empty)
    };

    // nullopt when there is no property with that name (searched along the class or struct chain).
    auto describe(const Target& target, std::wstring_view name) -> std::optional<PropertyInfo>;

    // Throws PropertyNotFoundError, or UEError for an unsupported property type.
    auto get(const Target& target, std::wstring_view name) -> Value;

    // `value.category` must match the property's category; scalars must already be range checked by the
    // caller. Struct properties are not written through here, see copy_struct().
    auto set(const Target& target, std::wstring_view name, const Value& value) -> void;

    // --- TArray properties ---
    // Elements are read and written by index; every call looks the array up again, so nothing dangles when the engine
    // reallocates it. A struct element is returned as an owned copy (assign a struct of the same type to change one).
    // Throws PropertyNotFoundError / UEError (not an array, unsupported element type,
    // index out of range).
    struct ArrayInfo
    {
        std::size_t length{0};
        PropertyInfo element{}; // category Unsupported when the element type is not supported
    };
    auto array_info(const Target& target, std::wstring_view name) -> ArrayInfo;
    auto array_get(const Target& target, std::wstring_view name, std::int64_t index) -> Value;
    auto array_set(const Target& target, std::wstring_view name, std::int64_t index, const Value& value) -> void;

    // Replaces the elements [start, stop) of the array with `values` (each converted to the element type first, so a bad
    // value is reported before anything changes). One primitive for append (start = stop = length), insert, delete,
    // slice assignment, clear and whole-array assignment. 0 <= start <= stop <= length. The engine grows and shrinks the
    // array with its own allocator. Resizing an array the engine is iterating at the moment (inside a hook on a function
    // that walks it) is up to the caller.
    auto array_replace(const Target& target, std::wstring_view name, std::int64_t start, std::int64_t stop, const std::vector<Value>& values) -> void;

    // --- fixed-size array properties (ArrayDim > 1) ---
    // The elements sit inline in the owner, so the length never changes. ArrayInfo::length is the dimension and
    // ArrayInfo::element the element type. A struct element is read as a view into the owner (its address is stable).
    // Throws PropertyNotFoundError / UEError (not a fixed-size array, unsupported element type, index out of range).
    auto fixed_info(const Target& target, std::wstring_view name) -> ArrayInfo;
    auto fixed_get(const Target& target, std::wstring_view name, std::int64_t index) -> Value;
    auto fixed_set(const Target& target, std::wstring_view name, std::int64_t index, const Value& value) -> void;

    // --- TMap and TSet properties ---
    // Like arrays, every call looks the container up again. Keys and elements may be numbers, bool, FName, FString and
    // objects; values may also be structs (returned as owned copies). Struct keys can be listed but not looked up or added.
    // Anything given to a mutating call is converted into a scratch element first, so a bad item changes nothing.
    // Iteration order is the engine's storage order. Throws PropertyNotFoundError / UEError.
    struct MapInfo
    {
        std::size_t length{0};
        PropertyInfo key{};
        PropertyInfo value{};
    };
    struct MapEntry
    {
        Value key{};
        Value value{};
    };
    auto map_info(const Target& target, std::wstring_view name) -> MapInfo;
    auto map_entries(const Target& target, std::wstring_view name) -> std::vector<MapEntry>;
    auto map_find(const Target& target, std::wstring_view name, const Value& key) -> std::optional<Value>;
    // Adds or overwrites each entry; with replace_all the map is emptied first.
    auto map_update(const Target& target, std::wstring_view name, const std::vector<MapEntry>& entries, bool replace_all) -> void;
    auto map_remove(const Target& target, std::wstring_view name, const Value& key) -> bool; // false if absent

    struct SetInfo
    {
        std::size_t length{0};
        PropertyInfo element{};
    };
    auto set_info(const Target& target, std::wstring_view name) -> SetInfo;
    auto set_values(const Target& target, std::wstring_view name) -> std::vector<Value>;
    auto set_contains(const Target& target, std::wstring_view name, const Value& value) -> bool;
    auto set_update(const Target& target, std::wstring_view name, const std::vector<Value>& values, bool replace_all) -> void;
    auto set_remove(const Target& target, std::wstring_view name, const Value& value) -> bool; // false if absent

    // TEST ONLY (ue4ss._selftest_poison_pointer): overwrite a plain object pointer property with a deliberately bad value, to
    // prove that reading it is safe. mode 0 freed heap, 1 unmapped address, 2 inside a live object, 3 misaligned, 4 small integer.
    auto poison_object_pointer(const Target& target, std::wstring_view name, int mode) -> void;

    // dest.<name> = source, where both are the same struct type. Throws UEError otherwise.
    auto copy_struct(const Target& dest, std::wstring_view name, const Target& source) -> void;

    // A new default-initialised struct value of the given UScriptStruct, in memory the caller owns.
    auto make_struct(const ObjectRef& script_struct) -> Target;

    // A zeroed, default-initialised parameter block for a UFunction: laid out exactly like a struct whose fields are the
    // function's parameters (inputs, outputs and the return value), so the ordinary property access reads and writes it.
    auto make_param_block(const ObjectRef& function) -> Target;

    // Number of hardware faults inside engine calls that call_function turned into a UEError.
    auto faults_survived() -> std::uint64_t;

    // Runs `function` on `context` with the parameter block `block` (made by make_param_block for the same function).
    // The engine fills the outputs and the return value into the block. `context` must be an instance of the function's
    // class unless the function is static. The caller releases the GIL around this call.
    auto call_function(const ObjectRef& context, const ObjectRef& function, const Target& block) -> void;

    // A new owned copy of a struct value (a view, or another owned struct).
    auto duplicate_struct(const Target& source) -> Target;

    // Counts for checking that owned structs are released: live = created - destroyed.
    struct OwnedStructStats
    {
        std::uint64_t created{0};
        std::uint64_t destroyed{0};      // buffers freed (inline or from the queue)
        std::uint64_t queued{0};         // destructions that had to be queued to the game thread
        std::uint64_t leaked_inner{0};   // buffers freed without DestroyStruct because the struct type was gone
        std::uint64_t leaked_block{0};   // buffers never freed because no game thread was available
    };
    auto owned_struct_stats() -> OwnedStructStats;

    // True if the target's memory belongs to a script-owned struct (make_struct, copy, a parameter block); false for a
    // view into an object, and for borrowed hook parameters (see ScopedParams).
    auto owns_memory(const Target& target) -> bool;

    // True for parameters borrowed from a running hook: valid only until the ScopedParams that made them is destroyed.
    auto is_scoped(const Target& target) -> bool;

    // Hook parameters: a struct-like view of memory the engine owns (the callee's Locals), laid out like `function`'s
    // parameter block. Reads and writes go straight to that memory while the object lives; the destructor marks it dead,
    // after which every access through any view derived from it throws InvalidObjectError. Copy a view to keep a value.
    class ScopedParams
    {
    public:
        ScopedParams(const ObjectRef& function, void* locals);
        ~ScopedParams();
        ScopedParams(const ScopedParams&) = delete;
        auto operator=(const ScopedParams&) -> ScopedParams& = delete;

        auto target() const -> const Target& { return m_target; }

        // Tells the view where an out parameter really lives (FFrame::OutParms): reads and writes of that parameter then
        // go to the caller's storage. `property` is the FProperty* (kept untyped to keep Unreal out of this header).
        auto add_out_parameter(const void* property, std::uint8_t* address) -> void;

    private:
        Target m_target{};
    };

    // Names of every property of the target (class chain or struct chain), supported or not.
    auto property_names(const Target& target) -> std::vector<std::wstring>;

    // False once the owner or the struct type is gone. Never throws.
    auto is_valid(const Target& target) -> bool;

    // The struct type's name (only for struct targets).
    auto struct_name(const Target& target) -> std::wstring;
}
