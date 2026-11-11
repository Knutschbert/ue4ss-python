#pragma once

#include <cstdint>
#include <optional>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include <pyue/name_id.hpp>

#include "ObjectRegistry.hpp"

// Operations on Unreal objects behind ObjectRef handles. Python-free; includes no Unreal headers.
// Every function requires the game thread and resolves the handle first, so a dead object throws
// InvalidObjectError instead of crashing (docs/LIFETIME.md).
namespace pyue::objects::api
{
    // A null ObjectRef (handle.index < 0) means "not found" / "no such object".
    auto find_by_path(std::wstring_view path) -> ObjectRef;
    auto find_first_of(std::wstring_view class_name) -> ObjectRef;
    auto object_at_address(std::uintptr_t address) -> ObjectRef; // the live object at that address, or an empty ref (never faults)

    // Every live instance of the class or a subclass; default objects and archetypes are left out. A class name that does not exist gives an empty list
    // without adding the name to the engine's name table.
    auto find_all_of(std::wstring_view class_name) -> std::vector<ObjectRef>;

    // Objects matching a class (and its subclasses unless exact_class), an object name, or both; at least one must be
    // given. Unlike find_all_of this includes default objects unless banned_flags excludes them. required_flags: all must
    // be set; banned_flags: none may be. limit 0 means no limit. Unknown names give an empty list.
    auto find_objects(std::optional<std::wstring_view> class_name,
                      std::optional<std::wstring_view> object_name,
                      std::int32_t required_flags,
                      std::int32_t banned_flags,
                      bool exact_class,
                      std::size_t limit) -> std::vector<ObjectRef>;

    auto full_name(const ObjectRef& ref) -> std::wstring;
    auto fname(const ObjectRef& ref) -> NameId;
    auto class_of(const ObjectRef& ref) -> ObjectRef;
    auto outer_of(const ObjectRef& ref) -> ObjectRef;
    auto address(const ObjectRef& ref) -> std::uintptr_t;

    // What kind of Unreal object this is; picks the Python wrapper type.
    enum class ObjectKind
    {
        Object,
        Struct, // a UStruct that is not one of the more specific kinds below
        Class,
        ScriptStruct,
        Function,
        Enum,
    };
    auto kind_of(const ObjectRef& ref) -> ObjectKind;

    // --- UStruct (classes, script structs and functions are all structs) ---
    auto super_of(const ObjectRef& ref) -> ObjectRef;                              // null ref if none
    auto field_names(const ObjectRef& ref) -> std::vector<std::wstring>;           // properties along the chain
    auto function_names(const ObjectRef& ref) -> std::vector<std::wstring>;        // functions along the chain
    auto find_function(const ObjectRef& ref, std::wstring_view name) -> ObjectRef; // null ref if none

    // --- calling functions ---
    // The function `name` that `object` can call: for an instance, a function of its class (or a parent); for a class
    // object, a function of that class itself (typically a static function of a Blueprint library). Null ref if none.
    auto find_method(const ObjectRef& object, std::wstring_view name) -> ObjectRef;
    // The object to run `function` on: the object itself, or for a class object the class default object (static
    // functions only; an instance function on a class object throws UEError).
    auto call_context(const ObjectRef& object, const ObjectRef& function) -> ObjectRef;

    // --- UClass ---
    auto cdo_of(const ObjectRef& ref) -> ObjectRef;
    auto is_child_of(const ObjectRef& ref, const ObjectRef& ancestor) -> bool;
    auto is_child_of_path(const ObjectRef& ref, std::wstring_view ancestor_path) -> bool;

    // --- reflection: what a property or parameter is (for typings and class wrappers) ---
    struct TypeInfo
    {
        std::string type_name{};      // the engine property class: "FloatProperty", "ObjectProperty", "ArrayProperty", ...
        std::wstring class_path{};    // object, weak/lazy/soft object and interface properties: the class; class and soft class properties: the meta class
        std::wstring struct_path{};   // struct properties
        std::wstring enum_path{};     // enum properties and enum-typed bytes
        std::vector<TypeInfo> inner{}; // TArray and TSet: the element; TMap: key then value
        int array_dim{1};             // more than 1 for a fixed-size array property
    };
    struct FieldInfo
    {
        std::wstring name{};
        TypeInfo type{};
        std::wstring declared_in{};   // path of the class or struct that declares the property
        std::uint64_t flags{0};       // the engine's EPropertyFlags
    };
    // The properties of a class or struct, in declaration order; parents' properties too when include_super.
    auto field_infos(const ObjectRef& ref, bool include_super) -> std::vector<FieldInfo>;

    // --- UFunction ---
    struct ParamInfo
    {
        std::wstring name{};
        TypeInfo type{};
        std::string type_name{}; // e.g. "FloatProperty", "ObjectProperty"
        bool is_out{false};
        bool is_return{false};
        bool is_const{false};
        bool is_reference{false};
    };
    auto function_flags(const ObjectRef& ref) -> std::uint32_t;
    auto function_params(const ObjectRef& ref) -> std::vector<ParamInfo>;
    auto function_script(const ObjectRef& ref) -> std::vector<std::uint8_t>; // the compiled Blueprint bytecode (UStruct::Script); empty for native functions

    // --- UEnum: (name, value) pairs with any "EnumName::" prefix removed ---
    auto enum_names(const ObjectRef& ref) -> std::vector<std::pair<std::wstring, std::int64_t>>;
    auto enum_name_by_value(const ObjectRef& ref, std::int64_t value) -> std::optional<std::wstring>;

    // `cls` must be a UClass; throws UEError otherwise.
    auto is_a(const ObjectRef& ref, const ObjectRef& cls) -> bool;
    // Looks the class up by path first; throws UEError if there is no such class.
    auto is_a_path(const ObjectRef& ref, std::wstring_view class_path) -> bool;
}
