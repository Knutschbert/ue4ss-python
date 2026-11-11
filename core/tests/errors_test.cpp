#include "test.hpp"

#include <pyue/errors.hpp>

#include <stdexcept>

template <typename Thrower>
static auto classify(Thrower thrower) -> pyue::ErrorInfo
{
    try
    {
        thrower();
    }
    catch (...)
    {
        return pyue::classify_current_exception();
    }
    return {};
}

int main()
{
    using pyue::ErrorKind;

    auto info = classify([] { throw pyue::PythonErrorSet{}; });
    PYUE_CHECK(info.kind == ErrorKind::PythonErrorSet);

    info = classify([] { throw pyue::InvalidObjectError("gone"); });
    PYUE_CHECK(info.kind == ErrorKind::InvalidObject);
    PYUE_CHECK(info.message == "gone");

    info = classify([] { throw pyue::PropertyNotFoundError("no Foo"); });
    PYUE_CHECK(info.kind == ErrorKind::PropertyNotFound);
    PYUE_CHECK(info.message == "no Foo");

    // The more derived type must win over its base class.
    info = classify([] { throw pyue::UEError("plain"); });
    PYUE_CHECK(info.kind == ErrorKind::UE);
    PYUE_CHECK(info.message == "plain");

    info = classify([] { throw std::bad_alloc(); });
    PYUE_CHECK(info.kind == ErrorKind::OutOfMemory);

    // bad_alloc is a std::exception too; it must not be reported as Internal.
    info = classify([] { throw std::runtime_error("boom"); });
    PYUE_CHECK(info.kind == ErrorKind::Internal);
    PYUE_CHECK(info.message == "boom");

    info = classify([] { throw 42; });
    PYUE_CHECK(info.kind == ErrorKind::Unknown);
    PYUE_CHECK(!info.message.empty());

    // UEError and InvalidObjectError are catchable as std::exception.
    bool caught = false;
    try { throw pyue::InvalidObjectError("x"); } catch (const std::exception&) { caught = true; }
    PYUE_CHECK(caught);

    return PYUE_TEST_MAIN_RESULT();
}
