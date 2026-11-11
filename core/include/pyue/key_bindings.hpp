#pragma once

#include <cstdint>
#include <mutex>
#include <set>
#include <utility>
#include <vector>

namespace pyue
{
    // Which Python callbacks listen to which key (plus modifier keys). UE4SS cannot remove a key event once registered, so the mod
    // registers ONE native handler per (key, modifiers) combination, the first time anyone asks, and this table decides what
    // that handler does: it hands out the ids that are active right now, in registration order. Unregistering an id only removes
    // it here. All members are safe to call from any thread (the native handler runs on UE4SS's event-loop thread).
    class KeyBindings
    {
    public:
        static constexpr std::uint8_t shift = 1;
        static constexpr std::uint8_t control = 2;
        static constexpr std::uint8_t alt = 4;

        // The modifier bit for a Windows virtual key (0x10 shift, 0x11 control, 0x12 alt); 0 for anything else.
        static auto modifier_bit(int virtual_key) -> std::uint8_t
        {
            return virtual_key == 0x10 ? shift : virtual_key == 0x11 ? control : virtual_key == 0x12 ? alt : 0;
        }

        struct Added
        {
            int id{0};
            bool needs_native{false}; // true the first time this (key, modifiers) combination appears: register the native handler now
        };

        auto add(std::uint8_t key, std::uint8_t modifiers) -> Added
        {
            std::lock_guard lock{m_mutex};
            Added added;
            added.id = ++m_last_id;
            m_bindings.push_back({added.id, key, modifiers});
            added.needs_native = m_native.insert({key, modifiers}).second;
            return added;
        }

        // False if there is no such id (never issued, or already removed).
        auto remove(int id) -> bool
        {
            std::lock_guard lock{m_mutex};
            for (auto it = m_bindings.begin(); it != m_bindings.end(); ++it)
            {
                if (it->id == id)
                {
                    m_bindings.erase(it);
                    return true;
                }
            }
            return false;
        }

        auto active(std::uint8_t key, std::uint8_t modifiers) const -> std::vector<int>
        {
            std::lock_guard lock{m_mutex};
            std::vector<int> ids;
            for (const auto& binding : m_bindings)
            {
                if (binding.key == key && binding.modifiers == modifiers) ids.push_back(binding.id);
            }
            return ids;
        }

        auto size() const -> std::size_t
        {
            std::lock_guard lock{m_mutex};
            return m_bindings.size();
        }

        // Removes every binding (the native handlers stay registered and then do nothing).
        auto clear() -> void
        {
            std::lock_guard lock{m_mutex};
            m_bindings.clear();
        }

    private:
        struct Binding
        {
            int id;
            std::uint8_t key;
            std::uint8_t modifiers;
        };

        mutable std::mutex m_mutex;
        std::vector<Binding> m_bindings;
        std::set<std::pair<std::uint8_t, std::uint8_t>> m_native;
        int m_last_id{0};
    };
}
