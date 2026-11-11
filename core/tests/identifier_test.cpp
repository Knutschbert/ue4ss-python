#include "test.hpp"

#include <pyue/identifier.hpp>

#include <string>

int main()
{
    using pyue::python_identifier;
    using pyue::is_identifier_alias;

    PYUE_CHECK(python_identifier(std::string("Sun height")) == "Sun_height");
    PYUE_CHECK(python_identifier(std::string("Colors determined by sun position")) == "Colors_determined_by_sun_position");
    PYUE_CHECK(python_identifier(std::string("Get Total Duration Seconds")) == "Get_Total_Duration_Seconds");
    PYUE_CHECK(python_identifier(std::string("Already_fine")) == "Already_fine");
    PYUE_CHECK(python_identifier(std::string("a-b.c/d?")) == "a_b_c_d_");
    PYUE_CHECK(python_identifier(std::string("3D view")) == "_3D_view");   // a leading digit gets an underscore in front
    PYUE_CHECK(python_identifier(std::string("")) == "_");
    PYUE_CHECK(python_identifier(std::string("from")) == "from_");        // a keyword gets one behind
    PYUE_CHECK(python_identifier(std::string("None")) == "None_");
    PYUE_CHECK(python_identifier(std::string("print")) == "print");        // not a keyword
    PYUE_CHECK(python_identifier(std::string("Is Valid")) == "Is_Valid");
    PYUE_CHECK(python_identifier(std::wstring(L"Sun height")) == L"Sun_height");
    PYUE_CHECK(python_identifier(std::wstring(L"Grüße Welt")) == L"Grüße_Welt");   // letters above 127 stay

    PYUE_CHECK(is_identifier_alias(std::string_view("Sun_height"), std::string_view("Sun height")));
    PYUE_CHECK(!is_identifier_alias(std::string_view("Sun height"), std::string_view("Sun height")));    // the exact name is not an alias
    PYUE_CHECK(!is_identifier_alias(std::string_view("Sun_Height"), std::string_view("Sun height")));    // case matters
    PYUE_CHECK(!is_identifier_alias(std::string_view("Sun__height"), std::string_view("Sun height")));
    PYUE_CHECK(is_identifier_alias(std::string_view("from_"), std::string_view("from")));
    return PYUE_TEST_MAIN_RESULT();
}
