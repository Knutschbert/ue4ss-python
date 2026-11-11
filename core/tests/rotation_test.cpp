#include "test.hpp"

#include <cmath>
#include <random>

#include <pyue/rotation.hpp>

using namespace pyue::rotation;

static bool near(double a, double b, double tolerance = 1e-9) { return std::fabs(a - b) <= tolerance; }
static bool same_rotation(const Quat& a, const Quat& b, double tolerance = 1e-9)
{
    // q and -q are the same rotation
    const double dot = std::fabs(a.x * b.x + a.y * b.y + a.z * b.z + a.w * b.w);
    return 1.0 - dot <= tolerance;
}
static double angle_difference(double a, double b)
{
    double d = std::fmod(a - b, 360.0);
    if (d > 180.0) d -= 360.0;
    if (d < -180.0) d += 360.0;
    return std::fabs(d);
}

int main()
{
    {
        // Known FRotator::Quaternion() values (also what the engine gives in the test game)
        auto yaw = from_rotator(0, 90, 0);
        PYUE_CHECK(near(yaw.x, 0) && near(yaw.y, 0) && near(yaw.z, 0.7071067812) && near(yaw.w, 0.7071067812, 1e-9));
        auto pitch = from_rotator(90, 0, 0);
        PYUE_CHECK(near(pitch.x, 0) && near(pitch.y, -0.7071067812) && near(pitch.z, 0) && near(pitch.w, 0.7071067812));
        auto roll = from_rotator(0, 0, 90);
        PYUE_CHECK(near(roll.x, -0.7071067812) && near(roll.y, 0) && near(roll.z, 0) && near(roll.w, 0.7071067812));
        auto mixed = from_rotator(30, 60, 45);
        PYUE_CHECK(near(mixed.x, -0.2005621211) && near(mixed.y, -0.3919038373) && near(mixed.z, 0.3604234057) && near(mixed.w, 0.8223631719));
        auto other = from_rotator(-20, 135, 10);
        PYUE_CHECK(near(other.x, -0.1926658635) && near(other.y, -0.0130986961) && near(other.z, 0.9121731943) && near(other.w, 0.3614531129));
        auto identity = from_rotator(0, 0, 0);
        PYUE_CHECK(near(identity.w, 1) && near(identity.x, 0));
        auto radians = from_rotator(0, pi / 2, 0, false);
        PYUE_CHECK(same_rotation(radians, yaw));
    }
    {
        // the unreal order is the axis order "zyx" with (yaw, -pitch, -roll)
        std::mt19937 rng(11);
        std::uniform_real_distribution<double> angle(-180, 180);
        const auto zyx = *parse_axes("zyx");
        for (int i = 0; i < 500; ++i)
        {
            const double p = angle(rng), y = angle(rng), r = angle(rng);
            PYUE_CHECK(same_rotation(from_rotator(p, y, r), from_axes(zyx, y, -p, -r), 1e-12));
        }
    }
    {
        // rotator round trip, away from and at the poles
        std::mt19937 rng(5);
        std::uniform_real_distribution<double> any(-180, 180), pitch_range(-89, 89);
        for (int i = 0; i < 1000; ++i)
        {
            const Quat q = from_rotator(pitch_range(rng), any(rng), any(rng));
            const auto e = to_rotator(q);
            PYUE_CHECK(same_rotation(from_rotator(e[0], e[1], e[2]), q, 1e-12));
        }
        for (double pole : {90.0, -90.0})
        {
            const Quat q = from_rotator(pole, 40, 25);
            const auto e = to_rotator(q);
            PYUE_CHECK(near(e[0], pole, 1e-3));
            PYUE_CHECK(same_rotation(from_rotator(e[0], e[1], e[2]), q, 1e-9));
        }
        const auto zero = to_rotator(Quat{});
        PYUE_CHECK(near(zero[0], 0) && near(zero[1], 0) && near(zero[2], 0));
        const auto in_radians = to_rotator(from_rotator(10, 20, 30), false);
        PYUE_CHECK(near(in_radians[0], 10 * pi / 180, 1e-9) && near(in_radians[1], 20 * pi / 180, 1e-9) && near(in_radians[2], 30 * pi / 180, 1e-9));
    }
    {
        // every axis order: angles come back, and a known case
        std::mt19937 rng(23);
        std::uniform_real_distribution<double> any(-180, 180), middle(-89, 89);
        for (const char* order : {"xyz", "xzy", "yxz", "yzx", "zxy", "zyx"})
        {
            const auto axes = *parse_axes(order);
            for (int i = 0; i < 300; ++i)
            {
                const double a = any(rng), b = middle(rng), c = any(rng);
                const auto back = to_axes(from_axes(axes, a, b, c), axes);
                PYUE_CHECK(angle_difference(a, back[0]) < 1e-7 && near(b, back[1], 1e-7) && angle_difference(c, back[2]) < 1e-7);
            }
        }
        // a quarter turn about one axis is that axis' quaternion
        const auto x90 = from_axes(*parse_axes("xyz"), 90, 0, 0);
        PYUE_CHECK(near(x90.x, 0.7071067812) && near(x90.w, 0.7071067812) && near(x90.y, 0) && near(x90.z, 0));
        const auto z90 = from_axes(*parse_axes("zyx"), 90, 0, 0);
        PYUE_CHECK(near(z90.z, 0.7071067812) && near(z90.w, 0.7071067812));
        // gimbal lock: the pair a, c collapses into a, the rotation is still reproduced
        const auto locked = from_axes(*parse_axes("xyz"), 30, 90, 20);
        const auto angles = to_axes(locked, *parse_axes("xyz"));
        PYUE_CHECK(near(angles[1], 90, 1e-4));
        PYUE_CHECK(same_rotation(from_axes(*parse_axes("xyz"), angles[0], angles[1], angles[2]), locked, 1e-9));
    }
    {
        PYUE_CHECK(parse_axes("xyz").has_value() && parse_axes("ZYX").has_value());
        PYUE_CHECK(!parse_axes("xxy").has_value());
        PYUE_CHECK(!parse_axes("xy").has_value());
        PYUE_CHECK(!parse_axes("xyzz").has_value());
        PYUE_CHECK(!parse_axes("abc").has_value());
        PYUE_CHECK(!parse_axes("").has_value());
        // a zero quaternion is read as no rotation, not as NaN
        const auto e = to_axes(Quat{0, 0, 0, 0}, *parse_axes("xyz"));
        PYUE_CHECK(near(e[0], 0) && near(e[1], 0) && near(e[2], 0));
    }
    return PYUE_TEST_MAIN_RESULT();
}
