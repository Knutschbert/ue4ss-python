#pragma once

#include <array>
#include <cmath>
#include <optional>
#include <string>

// Euler angles <-> quaternions, free of Unreal and Python so that the formulas can be tested exhaustively.
//
// Conventions (checked against the engine in the test game, see python/examples/euler_main.py):
//   * Quaternion q = (x, y, z, w), the layout of FQuat. The product is the Hamilton product; q = a * b applies b first and then a.
//   * "unreal" order: the angles are (pitch, yaw, roll) in degrees, exactly FRotator::Quaternion(): pitch is about Y, yaw about Z and
//     roll about X, and the result equals  Qz(yaw) * Qy(-pitch) * Qx(-roll)  where Qaxis(angle) = (axis * sin(angle / 2), cos(angle / 2)).
//   * An axis order such as "xyz" or "zyx" (any permutation of x, y, z): three INTRINSIC rotations, the first about the object's x axis
//     by `a`, then about the new y axis by `b`, then about the new z axis by `c`, i.e. q = Qx(a) * Qy(b) * Qz(c). The sign of an angle
//     is that of FQuat(Axis, Angle).
namespace pyue::rotation
{
    constexpr double pi = 3.14159265358979323846;

    struct Quat
    {
        double x{0}, y{0}, z{0}, w{1};
    };

    inline auto operator*(const Quat& a, const Quat& b) -> Quat
    {
        return Quat{a.w * b.x + a.x * b.w + a.y * b.z - a.z * b.y, a.w * b.y - a.x * b.z + a.y * b.w + a.z * b.x,
                    a.w * b.z + a.x * b.y - a.y * b.x + a.z * b.w, a.w * b.w - a.x * b.x - a.y * b.y - a.z * b.z};
    }

    inline auto normalized(const Quat& q) -> Quat
    {
        const double length = std::sqrt(q.x * q.x + q.y * q.y + q.z * q.z + q.w * q.w);
        if (length < 1e-12) return Quat{};
        return Quat{q.x / length, q.y / length, q.z / length, q.w / length};
    }

    // Rotation of `angle` radians about axis 0 (x), 1 (y) or 2 (z).
    inline auto about_axis(int axis, double angle_radians) -> Quat
    {
        const double s = std::sin(angle_radians / 2), c = std::cos(angle_radians / 2);
        return Quat{axis == 0 ? s : 0.0, axis == 1 ? s : 0.0, axis == 2 ? s : 0.0, c};
    }

    inline auto to_radians(double angle, bool degrees) -> double { return degrees ? angle * pi / 180.0 : angle; }
    inline auto from_radians(double angle, bool degrees) -> double { return degrees ? angle * 180.0 / pi : angle; }

    // FRotator::Quaternion().
    inline auto from_rotator(double pitch, double yaw, double roll, bool degrees = true) -> Quat
    {
        const double h = 0.5;
        const double sp = std::sin(to_radians(pitch, degrees) * h), cp = std::cos(to_radians(pitch, degrees) * h);
        const double sy = std::sin(to_radians(yaw, degrees) * h), cy = std::cos(to_radians(yaw, degrees) * h);
        const double sr = std::sin(to_radians(roll, degrees) * h), cr = std::cos(to_radians(roll, degrees) * h);
        return Quat{cr * sp * sy - sr * cp * cy, -cr * sp * cy - sr * cp * sy, cr * cp * sy - sr * sp * cy, cr * cp * cy + sr * sp * sy};
    }

    // FQuat::Rotator(): (pitch, yaw, roll). Handles the poles (pitch of +-90 degrees) like the engine does.
    inline auto to_rotator(const Quat& q, bool degrees = true) -> std::array<double, 3>
    {
        const double singularity = q.z * q.x - q.w * q.y;
        const double yaw_y = 2.0 * (q.w * q.z + q.x * q.y);
        const double yaw_x = 1.0 - 2.0 * (q.y * q.y + q.z * q.z);
        constexpr double threshold = 0.4999995;
        double pitch, yaw, roll;
        auto normalize_axis = [](double angle) {
            angle = std::fmod(angle, 360.0);
            if (angle > 180.0) angle -= 360.0;
            if (angle < -180.0) angle += 360.0;
            return angle;
        };
        yaw = std::atan2(yaw_y, yaw_x) * 180.0 / pi;
        if (singularity < -threshold)
        {
            pitch = -90.0;
            roll = normalize_axis(-yaw - (2.0 * std::atan2(q.x, q.w) * 180.0 / pi));
        }
        else if (singularity > threshold)
        {
            pitch = 90.0;
            roll = normalize_axis(yaw - (2.0 * std::atan2(q.x, q.w) * 180.0 / pi));
        }
        else
        {
            pitch = std::asin(2.0 * singularity) * 180.0 / pi;
            roll = std::atan2(-2.0 * (q.w * q.x + q.y * q.z), 1.0 - 2.0 * (q.x * q.x + q.y * q.y)) * 180.0 / pi;
        }
        return {from_radians(pitch * pi / 180.0, degrees), from_radians(yaw * pi / 180.0, degrees), from_radians(roll * pi / 180.0, degrees)};
    }

    // "xyz", "zyx", ... : the three axes in order, each used once. nullopt for anything else.
    inline auto parse_axes(const std::string& order) -> std::optional<std::array<int, 3>>
    {
        if (order.size() != 3) return std::nullopt;
        std::array<int, 3> axes{};
        bool seen[3] = {false, false, false};
        for (int i = 0; i < 3; ++i)
        {
            const char c = order[static_cast<std::size_t>(i)];
            const int axis = (c == 'x' || c == 'X') ? 0 : (c == 'y' || c == 'Y') ? 1 : (c == 'z' || c == 'Z') ? 2 : -1;
            if (axis < 0 || seen[axis]) return std::nullopt;
            seen[axis] = true;
            axes[static_cast<std::size_t>(i)] = axis;
        }
        return axes;
    }

    // Three intrinsic rotations in the given axis order: Q(axis0, a) * Q(axis1, b) * Q(axis2, c).
    inline auto from_axes(const std::array<int, 3>& axes, double a, double b, double c, bool degrees = true) -> Quat
    {
        return about_axis(axes[0], to_radians(a, degrees)) * about_axis(axes[1], to_radians(b, degrees)) * about_axis(axes[2], to_radians(c, degrees));
    }

    // The angles (a, b, c) that from_axes() turns back into q (b within +-90 degrees; at the poles c is set to 0).
    inline auto to_axes(const Quat& input, const std::array<int, 3>& axes, bool degrees = true) -> std::array<double, 3>
    {
        const Quat q = normalized(input);
        // rotation matrix of the Hamilton quaternion
        double m[3][3] = {{1 - 2 * (q.y * q.y + q.z * q.z), 2 * (q.x * q.y - q.z * q.w), 2 * (q.x * q.z + q.y * q.w)},
                          {2 * (q.x * q.y + q.z * q.w), 1 - 2 * (q.x * q.x + q.z * q.z), 2 * (q.y * q.z - q.x * q.w)},
                          {2 * (q.x * q.z - q.y * q.w), 2 * (q.y * q.z + q.x * q.w), 1 - 2 * (q.x * q.x + q.y * q.y)}};
        const int i = axes[0], j = axes[1], k = axes[2];
        // R = R_i(a) R_j(b) R_k(c). With sign s = +1 for cyclic orders (xyz, yzx, zxy) and -1 otherwise:
        // R[i][k] = s * sin(b)
        const double s = ((j - i + 3) % 3 == 1) ? 1.0 : -1.0;
        const double sin_b = std::fmax(-1.0, std::fmin(1.0, s * m[i][k]));
        const double b = std::asin(sin_b);
        double a, c;
        if (std::fabs(sin_b) < 0.9999999)
        {
            // R[j][k] = -s * sin(a) cos(b) ; R[k][k] = cos(a) cos(b) ; R[i][j] = -s * cos(b) sin(c) ; R[i][i] = cos(b) cos(c)
            a = std::atan2(-s * m[j][k], m[k][k]);
            c = std::atan2(-s * m[i][j], m[i][i]);
        }
        else
        {
            // gimbal lock: only a +- c is defined; put it all into a
            c = 0.0;
            a = std::atan2(s * m[k][j], m[j][j]);
        }
        return {from_radians(a, degrees), from_radians(b, degrees), from_radians(c, degrees)};
    }
}
