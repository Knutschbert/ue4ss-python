#pragma once

#include <cfloat>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstring>

// Reading and writing scalar property values in raw object memory, free of Unreal and Python.
// Everything here is what silently corrupts a game when it is wrong, so it lives in core/ where it can
// be tested with plain buffers.
namespace pyue
{
    enum class ScalarKind : std::uint8_t
    {
        Bool,
        Int8,
        Int16,
        Int32,
        Int64,
        UInt8,
        UInt16,
        UInt32,
        UInt64,
        Float,
        Double,
    };

    constexpr auto scalar_size(ScalarKind kind) -> std::size_t
    {
        switch (kind)
        {
        case ScalarKind::Bool: return 1;
        case ScalarKind::Int8: return 1;
        case ScalarKind::Int16: return 2;
        case ScalarKind::Int32: return 4;
        case ScalarKind::Int64: return 8;
        case ScalarKind::UInt8: return 1;
        case ScalarKind::UInt16: return 2;
        case ScalarKind::UInt32: return 4;
        case ScalarKind::UInt64: return 8;
        case ScalarKind::Float: return 4;
        case ScalarKind::Double: return 8;
        }
        return 0;
    }

    constexpr auto is_signed_int(ScalarKind kind) -> bool
    {
        return kind == ScalarKind::Int8 || kind == ScalarKind::Int16 || kind == ScalarKind::Int32 || kind == ScalarKind::Int64;
    }

    constexpr auto is_unsigned_int(ScalarKind kind) -> bool
    {
        return kind == ScalarKind::UInt8 || kind == ScalarKind::UInt16 || kind == ScalarKind::UInt32 || kind == ScalarKind::UInt64;
    }

    constexpr auto is_float(ScalarKind kind) -> bool
    {
        return kind == ScalarKind::Float || kind == ScalarKind::Double;
    }

    // Does `value` fit the integer kind (signed or unsigned) without changing its value?
    constexpr auto signed_fits(ScalarKind kind, std::int64_t value) -> bool
    {
        switch (kind)
        {
        case ScalarKind::Int8: return value >= INT8_MIN && value <= INT8_MAX;
        case ScalarKind::Int16: return value >= INT16_MIN && value <= INT16_MAX;
        case ScalarKind::Int32: return value >= INT32_MIN && value <= INT32_MAX;
        case ScalarKind::Int64: return true;
        case ScalarKind::UInt8: return value >= 0 && value <= UINT8_MAX;
        case ScalarKind::UInt16: return value >= 0 && value <= UINT16_MAX;
        case ScalarKind::UInt32: return value >= 0 && value <= UINT32_MAX;
        case ScalarKind::UInt64: return value >= 0;
        default: return false;
        }
    }

    constexpr auto unsigned_fits(ScalarKind kind, std::uint64_t value) -> bool
    {
        switch (kind)
        {
        case ScalarKind::Int8: return value <= static_cast<std::uint64_t>(INT8_MAX);
        case ScalarKind::Int16: return value <= static_cast<std::uint64_t>(INT16_MAX);
        case ScalarKind::Int32: return value <= static_cast<std::uint64_t>(INT32_MAX);
        case ScalarKind::Int64: return value <= static_cast<std::uint64_t>(INT64_MAX);
        case ScalarKind::UInt8: return value <= UINT8_MAX;
        case ScalarKind::UInt16: return value <= UINT16_MAX;
        case ScalarKind::UInt32: return value <= UINT32_MAX;
        case ScalarKind::UInt64: return true;
        default: return false;
        }
    }

    // A double fits a float property when it is finite and within float range; infinities and NaN are
    // representable and always fit. Precision loss is not an error.
    inline auto float_fits(ScalarKind kind, double value) -> bool
    {
        if (kind == ScalarKind::Double) return true;
        if (kind != ScalarKind::Float) return false;
        if (!std::isfinite(value)) return true;
        return std::fabs(value) <= static_cast<double>(FLT_MAX);
    }

    // How a UBoolProperty stores its value: a bit inside a byte (docs: FBoolProperty).
    // A native `bool` has field_mask 255 and byte_mask 1; a C++ bit-field has both equal to its bit.
    struct BoolLayout
    {
        std::uint8_t field_mask{1};
        std::uint8_t byte_mask{1};
        std::uint8_t byte_offset{0};
    };

    struct ScalarValue
    {
        enum class Type
        {
            Bool,
            Signed,
            Unsigned,
            Float
        };

        Type type{Type::Signed};
        bool boolean{false};
        std::int64_t signed_value{0};
        std::uint64_t unsigned_value{0};
        double float_value{0};

        static auto from_bool(bool v) -> ScalarValue { ScalarValue r; r.type = Type::Bool; r.boolean = v; return r; }
        static auto from_signed(std::int64_t v) -> ScalarValue { ScalarValue r; r.type = Type::Signed; r.signed_value = v; return r; }
        static auto from_unsigned(std::uint64_t v) -> ScalarValue { ScalarValue r; r.type = Type::Unsigned; r.unsigned_value = v; return r; }
        static auto from_float(double v) -> ScalarValue { ScalarValue r; r.type = Type::Float; r.float_value = v; return r; }
    };

    template <typename T>
    inline auto load(const void* p) -> T
    {
        T value;
        std::memcpy(&value, p, sizeof(T)); // properties are not guaranteed to be aligned for T
        return value;
    }

    template <typename T>
    inline auto store(void* p, T value) -> void
    {
        std::memcpy(p, &value, sizeof(T));
    }

    // `element` points at the property's value inside the object (ContainerPtrToValuePtr).
    inline auto read_scalar(ScalarKind kind, const void* element, BoolLayout bool_layout = {}) -> ScalarValue
    {
        switch (kind)
        {
        case ScalarKind::Bool: return ScalarValue::from_bool((static_cast<const std::uint8_t*>(element)[bool_layout.byte_offset] & bool_layout.field_mask) != 0);
        case ScalarKind::Int8: return ScalarValue::from_signed(load<std::int8_t>(element));
        case ScalarKind::Int16: return ScalarValue::from_signed(load<std::int16_t>(element));
        case ScalarKind::Int32: return ScalarValue::from_signed(load<std::int32_t>(element));
        case ScalarKind::Int64: return ScalarValue::from_signed(load<std::int64_t>(element));
        case ScalarKind::UInt8: return ScalarValue::from_unsigned(load<std::uint8_t>(element));
        case ScalarKind::UInt16: return ScalarValue::from_unsigned(load<std::uint16_t>(element));
        case ScalarKind::UInt32: return ScalarValue::from_unsigned(load<std::uint32_t>(element));
        case ScalarKind::UInt64: return ScalarValue::from_unsigned(load<std::uint64_t>(element));
        case ScalarKind::Float: return ScalarValue::from_float(static_cast<double>(load<float>(element)));
        case ScalarKind::Double: return ScalarValue::from_float(load<double>(element));
        }
        return {};
    }

    // Changes only the bit(s) of this property, never its neighbours in the same byte.
    inline auto write_bool(void* element, BoolLayout layout, bool value) -> void
    {
        auto* byte = static_cast<std::uint8_t*>(element) + layout.byte_offset;
        *byte = static_cast<std::uint8_t>((*byte & ~layout.field_mask) | (value ? layout.byte_mask : 0));
    }

    // The caller range-checks first (signed_fits / unsigned_fits / float_fits); these only store.
    inline auto write_signed(ScalarKind kind, void* element, std::int64_t value) -> void
    {
        switch (kind)
        {
        case ScalarKind::Int8: store(element, static_cast<std::int8_t>(value)); break;
        case ScalarKind::Int16: store(element, static_cast<std::int16_t>(value)); break;
        case ScalarKind::Int32: store(element, static_cast<std::int32_t>(value)); break;
        case ScalarKind::Int64: store(element, value); break;
        case ScalarKind::UInt8: store(element, static_cast<std::uint8_t>(value)); break;
        case ScalarKind::UInt16: store(element, static_cast<std::uint16_t>(value)); break;
        case ScalarKind::UInt32: store(element, static_cast<std::uint32_t>(value)); break;
        case ScalarKind::UInt64: store(element, static_cast<std::uint64_t>(value)); break;
        default: break;
        }
    }

    inline auto write_unsigned(ScalarKind kind, void* element, std::uint64_t value) -> void
    {
        switch (kind)
        {
        case ScalarKind::Int8: store(element, static_cast<std::int8_t>(value)); break;
        case ScalarKind::Int16: store(element, static_cast<std::int16_t>(value)); break;
        case ScalarKind::Int32: store(element, static_cast<std::int32_t>(value)); break;
        case ScalarKind::Int64: store(element, static_cast<std::int64_t>(value)); break;
        case ScalarKind::UInt8: store(element, static_cast<std::uint8_t>(value)); break;
        case ScalarKind::UInt16: store(element, static_cast<std::uint16_t>(value)); break;
        case ScalarKind::UInt32: store(element, static_cast<std::uint32_t>(value)); break;
        case ScalarKind::UInt64: store(element, value); break;
        default: break;
        }
    }

    inline auto write_float(ScalarKind kind, void* element, double value) -> void
    {
        if (kind == ScalarKind::Float) store(element, static_cast<float>(value));
        else if (kind == ScalarKind::Double) store(element, value);
    }
}
