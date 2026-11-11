#include "test.hpp"

#include <pyue/name_id.hpp>

#include <set>
#include <unordered_set>

int main()
{
    using pyue::NameId;

    PYUE_CHECK(NameId{}.is_none());
    PYUE_CHECK(!(NameId{0, 1}.is_none())); // "None_0" is a numbered name, not NAME_None
    PYUE_CHECK(!(NameId{5, 0}.is_none()));

    PYUE_CHECK((NameId{5, 2} == NameId{5, 2}));
    PYUE_CHECK((NameId{5, 2} != NameId{5, 3}));
    PYUE_CHECK((NameId{5, 2} != NameId{6, 2}));

    // Equal ids hash equally; swapping the fields gives a different hash.
    PYUE_CHECK(pyue::hash_name({5, 2}) == pyue::hash_name({5, 2}));
    PYUE_CHECK(pyue::hash_name({5, 2}) != pyue::hash_name({2, 5}));

    // Few collisions over a realistic range of indices and numbers (including negative numbers).
    std::unordered_set<std::size_t> hashes;
    int total = 0;
    for (std::uint32_t index = 0; index < 2000; ++index)
    {
        for (std::int32_t number = -2; number < 5; ++number)
        {
            hashes.insert(pyue::hash_name({index, number}));
            ++total;
        }
    }
    PYUE_CHECK(static_cast<int>(hashes.size()) == total);

    return PYUE_TEST_MAIN_RESULT();
}
