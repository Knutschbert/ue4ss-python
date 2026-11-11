#include "test.hpp"

#include <pyue/key_bindings.hpp>

#include <thread>

int main()
{
    using pyue::KeyBindings;
    {
        KeyBindings table;
        const auto first = table.add(0x70, 0);
        PYUE_CHECK(first.id == 1 && first.needs_native);       // first listener of F1: register the native handler
        const auto second = table.add(0x70, 0);
        PYUE_CHECK(second.id == 2 && !second.needs_native);    // the same combination reuses it
        PYUE_CHECK(table.add(0x70, KeyBindings::control).needs_native); // F1 with Ctrl is another combination
        PYUE_CHECK(table.add(0x71, 0).needs_native);
        const auto ids = table.active(0x70, 0);
        PYUE_CHECK(ids.size() == 2 && ids[0] == 1 && ids[1] == 2); // registration order
        PYUE_CHECK(table.active(0x70, KeyBindings::control).size() == 1);
        PYUE_CHECK(table.active(0x70, KeyBindings::shift).empty()); // exact modifier match only
    }
    {
        KeyBindings table;
        const int a = table.add(0x41, 0).id;
        const int b = table.add(0x41, 0).id;
        PYUE_CHECK(table.remove(a));
        PYUE_CHECK(!table.remove(a));                           // already gone
        PYUE_CHECK(!table.remove(999));                         // never issued
        PYUE_CHECK(table.active(0x41, 0) == std::vector<int>{b});
        PYUE_CHECK(!table.add(0x41, 0).needs_native);           // the native handler is still registered after removals
        table.remove(b);
        PYUE_CHECK(table.size() == 1);
        table.clear();
        PYUE_CHECK(table.size() == 0 && table.active(0x41, 0).empty());
        PYUE_CHECK(table.add(0x41, 0).id > b);                  // ids are never reused
    }
    {
        PYUE_CHECK(KeyBindings::modifier_bit(0x10) == KeyBindings::shift);
        PYUE_CHECK(KeyBindings::modifier_bit(0x11) == KeyBindings::control);
        PYUE_CHECK(KeyBindings::modifier_bit(0x12) == KeyBindings::alt);
        PYUE_CHECK(KeyBindings::modifier_bit(0x41) == 0);
    }
    {
        // many threads adding, removing and reading at once
        KeyBindings table;
        std::vector<std::thread> threads;
        for (int t = 0; t < 8; ++t)
        {
            threads.emplace_back([&table, t] {
                for (int i = 0; i < 500; ++i)
                {
                    const int id = table.add(static_cast<std::uint8_t>(0x30 + t), 0).id;
                    (void)table.active(static_cast<std::uint8_t>(0x30 + t), 0);
                    PYUE_CHECK(table.remove(id));
                }
            });
        }
        for (auto& thread : threads) thread.join();
        PYUE_CHECK(table.size() == 0);
    }
    return 0;
}
