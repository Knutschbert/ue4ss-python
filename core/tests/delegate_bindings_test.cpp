#include "test.hpp"

#include <pyue/delegate_bindings.hpp>

#include <thread>
#include <vector>

int main()
{
    using pyue::DelegateBindings;
    {
        DelegateBindings table;
        // First binding of a signature function and a (signature, object) pair: both must be installed.
        const auto a = table.add(100, 1);
        PYUE_CHECK(a.id == 1 && a.needs_hook && a.needs_entry);
        // A second callback on the same (signature, object) reuses the hook and the engine entry.
        const auto b = table.add(100, 1);
        PYUE_CHECK(b.id == 2 && !b.needs_hook && !b.needs_entry);
        // A different object with the same signature reuses the hook but needs its own entry.
        const auto c = table.add(100, 2);
        PYUE_CHECK(c.id == 3 && !c.needs_hook && c.needs_entry);
        // A different signature needs a new hook and a new entry.
        const auto d = table.add(200, 1);
        PYUE_CHECK(d.id == 4 && d.needs_hook && d.needs_entry);
        // active() returns registrations for the exact (signature, object) only.
        const auto ids1 = table.active(100, 1);
        const auto ids3 = table.active(100, 2);
        const auto ids4 = table.active(200, 1);
        const std::vector<int> expect12 = {1, 2};
        PYUE_CHECK(ids1 == expect12);
        PYUE_CHECK(ids3 == std::vector<int>{3});
        PYUE_CHECK(ids4 == std::vector<int>{4});
        PYUE_CHECK(table.active(100, 99).empty());
        PYUE_CHECK(table.active(99, 1).empty());
        PYUE_CHECK(table.size() == 4);
    }

    {
        DelegateBindings table;
        const auto a = table.add(100, 1);
        const auto b = table.add(100, 1); // same pair
        const auto c = table.add(100, 2); // same signature, other object
        // Removing a non-first callback of a pair leaves the entry and the hook installed.
        DelegateBindings::Removed rem;
        PYUE_CHECK(table.remove(b.id, &rem));
        PYUE_CHECK(!rem.last_for_signature && !rem.last_for_pair);
        PYUE_CHECK(table.active(100, 1) == std::vector<int>{a.id});

        // Removing the last callback of the (100,1) pair drops the entry but keeps the hook (100 still used by object 2).
        rem = DelegateBindings::Removed{};
        PYUE_CHECK(table.remove(a.id, &rem));
        PYUE_CHECK(rem.last_for_pair && !rem.last_for_signature);
        PYUE_CHECK(table.active(100, 1).empty());
        PYUE_CHECK(table.active(100, 2) == std::vector<int>{c.id});

        // Removing is idempotent and rejects unknown ids.
        PYUE_CHECK(!table.remove(b.id));
        PYUE_CHECK(!table.remove(999));

        // Removing the last user of the signature drops the hook too.
        rem = DelegateBindings::Removed{};
        PYUE_CHECK(table.remove(c.id, &rem));
        PYUE_CHECK(rem.last_for_signature && rem.last_for_pair);
        PYUE_CHECK(table.size() == 0);
    }

    {
        // The first binding of a fresh signature always gets needs_hook true, even after a previous signature's hook was dropped.
        DelegateBindings table;
        const auto a = table.add(100, 1);
        DelegateBindings::Removed rem;
        table.remove(a.id, &rem);
        PYUE_CHECK(rem.last_for_signature && rem.last_for_pair);
        const auto b = table.add(100, 1);
        PYUE_CHECK(b.needs_hook && b.needs_entry); // a clean signature re-arms the hook
        PYUE_CHECK(table.active(100, 1) == std::vector<int>{b.id});
        table.clear();
        PYUE_CHECK(table.size() == 0 && table.active(100, 1).empty());
    }

    {
        // Many threads adding, removing and reading at once.
        DelegateBindings table;
        std::vector<std::thread> threads;
        for (int t = 0; t < 8; ++t)
        {
            threads.emplace_back([&table, t] {
                for (int i = 0; i < 500; ++i)
                {
                    const auto added = table.add(100 + t, 1 + (i % 3));
                    (void)table.active(100 + t, 1 + (i % 3));
                    (void)table.remove(added.id);
                }
            });
        }
        for (auto& thread : threads) thread.join();
        PYUE_CHECK(table.size() == 0);
    }
    return 0;
}
