#pragma once

#include <cstdint>
#include <mutex>
#include <set>
#include <utility>
#include <vector>

namespace pyue
{
    // Which Python callbacks listen to which multicast delegate (event dispatcher). The mod registers ONE engine delegate
    // entry per (signature function, object) pair and ONE engine hook per signature function; this table decides what they
    // do. `add` reports when a new engine entry or a new signature hook must be installed; `active` lists which binding ids
    // should run when a particular (signature function, object) broadcasts. All members are safe to call from any thread
    // (the hooked broadcast reaches the mod on the game thread, where binding and unbinding also happen).
    class DelegateBindings
    {
    public:
        struct Added
        {
            int id{0};
            bool needs_hook{false};  // true the first time this signature function appears at all: install the native hook on it
            bool needs_entry{false}; // true the first time this (signature, object) pair appears: add the engine delegate entry
        };

        struct Removed
        {
            bool last_for_signature{false}; // no binding uses this signature any more: remove the native hook
            bool last_for_pair{false};      // no binding uses this (signature, object) pair any more: remove the engine entry
        };

        auto add(std::uint64_t signature, std::uint64_t object) -> Added
        {
            std::lock_guard lock{m_mutex};
            Added added;
            added.id = ++m_last_id;
            m_bindings.push_back({added.id, signature, object});
            added.needs_hook = m_hooked_signatures.insert(signature).second;
            added.needs_entry = m_entries.insert({signature, object}).second;
            return added;
        }

        // False if there is no such id (never issued, or already removed). Otherwise `removed` (when given) is filled with what
        // the removal makes empty so the caller can tear down the engine side.
        auto remove(int id, Removed* removed = nullptr) -> bool
        {
            std::lock_guard lock{m_mutex};
            for (auto it = m_bindings.begin(); it != m_bindings.end(); ++it)
            {
                if (it->id == id)
                {
                    const std::uint64_t signature = it->signature;
                    const std::uint64_t object = it->object;
                    m_bindings.erase(it);
                    if (removed)
                    {
                        removed->last_for_signature = !any_for(signature);
                        removed->last_for_pair = !any_for(signature, object);
                        if (removed->last_for_signature) m_hooked_signatures.erase(signature);
                        if (removed->last_for_pair) m_entries.erase({signature, object});
                    }
                    return true;
                }
            }
            return false;
        }

        // The ids that should run when (signature, object) broadcasts, in registration order.
        auto active(std::uint64_t signature, std::uint64_t object) const -> std::vector<int>
        {
            std::lock_guard lock{m_mutex};
            std::vector<int> ids;
            for (const auto& binding : m_bindings)
            {
                if (binding.signature == signature && binding.object == object) ids.push_back(binding.id);
            }
            return ids;
        }

        auto size() const -> std::size_t
        {
            std::lock_guard lock{m_mutex};
            return m_bindings.size();
        }

        // Removes every binding (engine entries and hooks stay until torn down by the caller; then they do nothing).
        auto clear() -> void
        {
            std::lock_guard lock{m_mutex};
            m_bindings.clear();
        }

    private:
        auto any_for(std::uint64_t signature) const -> bool
        {
            for (const auto& binding : m_bindings)
            {
                if (binding.signature == signature) return true;
            }
            return false;
        }

        auto any_for(std::uint64_t signature, std::uint64_t object) const -> bool
        {
            for (const auto& binding : m_bindings)
            {
                if (binding.signature == signature && binding.object == object) return true;
            }
            return false;
        }

        struct Binding
        {
            int id;
            std::uint64_t signature;
            std::uint64_t object;
        };

        mutable std::mutex m_mutex;
        std::vector<Binding> m_bindings;
        std::set<std::uint64_t> m_hooked_signatures;
        std::set<std::pair<std::uint64_t, std::uint64_t>> m_entries;
        int m_last_id{0};
    };
}
