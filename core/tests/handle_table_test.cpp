#include "test.hpp"

#include <pyue/handle_table.hpp>

#include <atomic>
#include <thread>
#include <vector>

int main()
{
    {
        // Equal handles hash equally; any differing field changes the hash.
        const pyue::Handle base{5, 2, 1};
        PYUE_CHECK(pyue::hash_handle(base) == pyue::hash_handle(pyue::Handle{5, 2, 1}));
        PYUE_CHECK(pyue::hash_handle(base) != pyue::hash_handle(pyue::Handle{6, 2, 1}));
        PYUE_CHECK(pyue::hash_handle(base) != pyue::hash_handle(pyue::Handle{5, 3, 1}));
        PYUE_CHECK(pyue::hash_handle(base) != pyue::hash_handle(pyue::Handle{5, 2, 2}));
    }
    {
        // A fresh handle is current; the default handle never is.
        pyue::HandleTable table;
        const auto handle = table.acquire(5);
        PYUE_CHECK(handle.index == 5);
        PYUE_CHECK(table.is_current(handle));
        PYUE_CHECK(!table.is_current(pyue::Handle{}));
        PYUE_CHECK(table.size() == 6);
    }
    {
        // Deletion invalidates handles of that slot only.
        pyue::HandleTable table;
        const auto a = table.acquire(1);
        const auto b = table.acquire(2);
        table.on_deleted(1);
        PYUE_CHECK(!table.is_current(a));
        PYUE_CHECK(table.is_current(b));
    }
    {
        // Slot reuse: the old handle stays invalid, a handle for the new occupant is valid.
        pyue::HandleTable table;
        const auto old_handle = table.acquire(3);
        table.on_deleted(3);
        const auto new_handle = table.acquire(3);
        PYUE_CHECK(!table.is_current(old_handle));
        PYUE_CHECK(table.is_current(new_handle));
        PYUE_CHECK(old_handle != new_handle);
        table.on_deleted(3);
        PYUE_CHECK(!table.is_current(new_handle));
        PYUE_CHECK(!table.is_current(old_handle));
    }
    {
        // Deleting a slot nobody holds a handle for is a no-op and does not grow the table.
        pyue::HandleTable table;
        table.on_deleted(1000);
        PYUE_CHECK(table.size() == 0);
        const auto handle = table.acquire(1000);
        PYUE_CHECK(table.is_current(handle));
    }
    {
        // Invalid indices.
        pyue::HandleTable table;
        PYUE_CHECK(table.acquire(-1).index == -1);
        PYUE_CHECK(!table.is_current(table.acquire(-1)));
        table.on_deleted(-1); // must not crash
        PYUE_CHECK(table.size() == 0);
    }
    {
        // An absurd index must not make the table allocate: it yields an invalid handle instead.
        pyue::HandleTable table;
        const auto huge = table.acquire(pyue::HandleTable::max_slots);
        PYUE_CHECK(huge.index == -1);
        PYUE_CHECK(!table.is_current(huge));
        PYUE_CHECK(table.acquire(2'000'000'000).index == -1);
        PYUE_CHECK(table.size() == 0);
        const auto last = table.acquire(pyue::HandleTable::max_slots - 1);
        PYUE_CHECK(last.index == pyue::HandleTable::max_slots - 1);
        PYUE_CHECK(table.is_current(last));
    }
    {
        // Shutdown makes every handle stale, including ones acquired before and re-acquired slots.
        pyue::HandleTable table;
        const auto before = table.acquire(7);
        table.shutdown();
        PYUE_CHECK(!table.is_current(before));
        const auto after = table.acquire(7);
        PYUE_CHECK(table.is_current(after));
        PYUE_CHECK(!table.is_current(before));
    }
    {
        // A handle for an index beyond the table is not current.
        pyue::HandleTable table;
        PYUE_CHECK(!table.is_current(pyue::Handle{42, 0, 0}));
    }
    {
        // Concurrent deletes, acquires and checks. A handle acquired before its slot's last
        // deletion must never report current afterwards.
        pyue::HandleTable table;
        constexpr int slots = 64;
        constexpr int rounds = 2000;
        std::atomic<bool> failed{false};

        std::vector<std::thread> threads;
        for (int t = 0; t < 4; ++t)
        {
            threads.emplace_back([&table, &failed, t] {
                for (int r = 0; r < rounds; ++r)
                {
                    const int slot = (r + t * 7) % slots;
                    const auto handle = table.acquire(slot);
                    table.on_deleted(slot);
                    if (table.is_current(handle)) failed = true; // our own delete happened before this check
                }
            });
        }
        for (auto& thread : threads) thread.join();
        PYUE_CHECK(!failed.load());
        PYUE_CHECK(table.size() == slots);
    }
    return PYUE_TEST_MAIN_RESULT();
}
