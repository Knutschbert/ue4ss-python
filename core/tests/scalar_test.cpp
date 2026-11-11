#include "test.hpp"

#include <pyue/scalar.hpp>

#include <cmath>
#include <limits>

using namespace pyue;

int main()
{
    // --- integer range checks at every boundary ---
    PYUE_CHECK(signed_fits(ScalarKind::Int8, 127) && !signed_fits(ScalarKind::Int8, 128));
    PYUE_CHECK(signed_fits(ScalarKind::Int8, -128) && !signed_fits(ScalarKind::Int8, -129));
    PYUE_CHECK(signed_fits(ScalarKind::Int16, 32767) && !signed_fits(ScalarKind::Int16, 32768));
    PYUE_CHECK(signed_fits(ScalarKind::Int16, -32768) && !signed_fits(ScalarKind::Int16, -32769));
    PYUE_CHECK(signed_fits(ScalarKind::Int32, 2147483647LL) && !signed_fits(ScalarKind::Int32, 2147483648LL));
    PYUE_CHECK(signed_fits(ScalarKind::Int32, -2147483648LL) && !signed_fits(ScalarKind::Int32, -2147483649LL));
    PYUE_CHECK(signed_fits(ScalarKind::Int64, std::numeric_limits<std::int64_t>::min()));
    PYUE_CHECK(signed_fits(ScalarKind::UInt8, 0) && signed_fits(ScalarKind::UInt8, 255) && !signed_fits(ScalarKind::UInt8, 256) && !signed_fits(ScalarKind::UInt8, -1));
    PYUE_CHECK(signed_fits(ScalarKind::UInt16, 65535) && !signed_fits(ScalarKind::UInt16, 65536) && !signed_fits(ScalarKind::UInt16, -1));
    PYUE_CHECK(signed_fits(ScalarKind::UInt32, 4294967295LL) && !signed_fits(ScalarKind::UInt32, 4294967296LL) && !signed_fits(ScalarKind::UInt32, -1));
    PYUE_CHECK(signed_fits(ScalarKind::UInt64, 0) && !signed_fits(ScalarKind::UInt64, -1));
    PYUE_CHECK(!signed_fits(ScalarKind::Float, 1) && !signed_fits(ScalarKind::Bool, 1)); // not integer kinds

    PYUE_CHECK(unsigned_fits(ScalarKind::Int8, 127) && !unsigned_fits(ScalarKind::Int8, 128));
    PYUE_CHECK(unsigned_fits(ScalarKind::Int32, 2147483647ULL) && !unsigned_fits(ScalarKind::Int32, 2147483648ULL));
    PYUE_CHECK(unsigned_fits(ScalarKind::Int64, 9223372036854775807ULL) && !unsigned_fits(ScalarKind::Int64, 9223372036854775808ULL));
    PYUE_CHECK(unsigned_fits(ScalarKind::UInt8, 255) && !unsigned_fits(ScalarKind::UInt8, 256));
    PYUE_CHECK(unsigned_fits(ScalarKind::UInt64, std::numeric_limits<std::uint64_t>::max()));

    // --- float range ---
    PYUE_CHECK(float_fits(ScalarKind::Float, 3.4e38) && !float_fits(ScalarKind::Float, 3.5e38) && !float_fits(ScalarKind::Float, -3.5e38));
    PYUE_CHECK(float_fits(ScalarKind::Float, std::numeric_limits<double>::infinity()));
    PYUE_CHECK(float_fits(ScalarKind::Float, std::numeric_limits<double>::quiet_NaN()));
    PYUE_CHECK(float_fits(ScalarKind::Float, 1e-300)); // underflows to 0, which is not an error
    PYUE_CHECK(float_fits(ScalarKind::Double, 1e300));
    PYUE_CHECK(!float_fits(ScalarKind::Int32, 1.0));

    // --- round trips through raw memory, at an unaligned address ---
    alignas(8) unsigned char buffer[24] = {};
    void* unaligned = buffer + 1;

    write_signed(ScalarKind::Int32, unaligned, -123456);
    PYUE_CHECK(read_scalar(ScalarKind::Int32, unaligned).signed_value == -123456);
    write_signed(ScalarKind::Int8, unaligned, -128);
    PYUE_CHECK(read_scalar(ScalarKind::Int8, unaligned).signed_value == -128);
    write_signed(ScalarKind::Int16, unaligned, 32767);
    PYUE_CHECK(read_scalar(ScalarKind::Int16, unaligned).signed_value == 32767);
    write_signed(ScalarKind::Int64, unaligned, std::numeric_limits<std::int64_t>::min());
    PYUE_CHECK(read_scalar(ScalarKind::Int64, unaligned).signed_value == std::numeric_limits<std::int64_t>::min());
    write_signed(ScalarKind::UInt8, unaligned, 200);
    PYUE_CHECK(read_scalar(ScalarKind::UInt8, unaligned).unsigned_value == 200);
    write_unsigned(ScalarKind::UInt32, unaligned, 4294967295ULL);
    PYUE_CHECK(read_scalar(ScalarKind::UInt32, unaligned).unsigned_value == 4294967295ULL);
    write_unsigned(ScalarKind::UInt64, unaligned, std::numeric_limits<std::uint64_t>::max());
    PYUE_CHECK(read_scalar(ScalarKind::UInt64, unaligned).unsigned_value == std::numeric_limits<std::uint64_t>::max());
    write_unsigned(ScalarKind::Int64, unaligned, 9223372036854775807ULL);
    PYUE_CHECK(read_scalar(ScalarKind::Int64, unaligned).signed_value == 9223372036854775807LL);
    write_unsigned(ScalarKind::UInt16, unaligned, 65535);
    PYUE_CHECK(read_scalar(ScalarKind::UInt16, unaligned).unsigned_value == 65535);
    write_signed(ScalarKind::UInt64, unaligned, 7);
    PYUE_CHECK(read_scalar(ScalarKind::UInt64, unaligned).unsigned_value == 7);

    write_float(ScalarKind::Float, unaligned, 1.5);
    PYUE_CHECK(read_scalar(ScalarKind::Float, unaligned).float_value == 1.5);
    write_float(ScalarKind::Float, unaligned, 0.1); // narrowed: reads back as the nearest float
    PYUE_CHECK(read_scalar(ScalarKind::Float, unaligned).float_value == static_cast<double>(0.1f));
    write_float(ScalarKind::Double, unaligned, 0.1);
    PYUE_CHECK(read_scalar(ScalarKind::Double, unaligned).float_value == 0.1);
    write_float(ScalarKind::Float, unaligned, -std::numeric_limits<double>::infinity());
    PYUE_CHECK(std::isinf(read_scalar(ScalarKind::Float, unaligned).float_value));
    write_float(ScalarKind::Double, unaligned, std::numeric_limits<double>::quiet_NaN());
    PYUE_CHECK(std::isnan(read_scalar(ScalarKind::Double, unaligned).float_value));

    // Writing must not touch bytes outside the value.
    for (auto& byte : buffer) byte = 0xAA;
    write_signed(ScalarKind::Int16, buffer + 4, 0x1234);
    PYUE_CHECK(buffer[3] == 0xAA && buffer[6] == 0xAA);
    write_float(ScalarKind::Float, buffer + 8, 2.0);
    PYUE_CHECK(buffer[7] == 0xAA && buffer[12] == 0xAA);

    // Sizes match the types.
    PYUE_CHECK(scalar_size(ScalarKind::Bool) == 1 && scalar_size(ScalarKind::Int16) == 2 && scalar_size(ScalarKind::Float) == 4 && scalar_size(ScalarKind::UInt64) == 8);

    // --- bool bit-fields: a native bool and eight bit-fields sharing a byte ---
    {
        unsigned char native = 0;
        const BoolLayout native_layout{255, 1, 0};
        PYUE_CHECK(!read_scalar(ScalarKind::Bool, &native, native_layout).boolean);
        write_bool(&native, native_layout, true);
        PYUE_CHECK(native == 1 && read_scalar(ScalarKind::Bool, &native, native_layout).boolean);
        write_bool(&native, native_layout, false);
        PYUE_CHECK(native == 0);
    }
    {
        unsigned char byte = 0;
        for (int bit = 0; bit < 8; ++bit)
        {
            const auto mask = static_cast<std::uint8_t>(1u << bit);
            write_bool(&byte, BoolLayout{mask, mask, 0}, true);
        }
        PYUE_CHECK(byte == 0xFF);

        // Clearing one bit leaves its seven neighbours alone.
        for (int bit = 0; bit < 8; ++bit)
        {
            unsigned char b = 0xFF;
            const auto mask = static_cast<std::uint8_t>(1u << bit);
            write_bool(&b, BoolLayout{mask, mask, 0}, false);
            PYUE_CHECK(b == static_cast<unsigned char>(0xFF & ~mask));
            PYUE_CHECK(!read_scalar(ScalarKind::Bool, &b, BoolLayout{mask, mask, 0}).boolean);
        }

        // Setting one bit leaves neighbours alone too.
        unsigned char c = 0b01010101;
        write_bool(&c, BoolLayout{0b00000010, 0b00000010, 0}, true);
        PYUE_CHECK(c == 0b01010111);
    }
    {
        // byte_offset selects a later byte of the property's storage.
        unsigned char bytes[3] = {0, 0, 0};
        write_bool(bytes, BoolLayout{0x04, 0x04, 2}, true);
        PYUE_CHECK(bytes[0] == 0 && bytes[1] == 0 && bytes[2] == 0x04);
        PYUE_CHECK(read_scalar(ScalarKind::Bool, bytes, BoolLayout{0x04, 0x04, 2}).boolean);
    }
    return PYUE_TEST_MAIN_RESULT();
}
