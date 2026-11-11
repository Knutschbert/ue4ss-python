#pragma once

#include <exception>
#include <new>
#include <stdexcept>
#include <string>

namespace pyue
{
    // Errors the bindings raise on purpose. They map to ue4ss.UEError / ue4ss.InvalidObjectError.
    struct UEError : std::runtime_error
    {
        using std::runtime_error::runtime_error;
    };

    // The wrapped Unreal object is gone (destroyed, garbage collected, slot reused).
    struct InvalidObjectError : UEError
    {
        using UEError::UEError;
    };

    // The object has no property with that name. Maps to AttributeError.
    struct PropertyNotFoundError : UEError
    {
        using UEError::UEError;
    };

    // Thrown after a Python exception has already been set by a Python API call, to unwind
    // C++ frames. The guard leaves the existing Python exception alone.
    struct PythonErrorSet
    {
    };

    enum class ErrorKind
    {
        PythonErrorSet,
        InvalidObject,
        PropertyNotFound,
        UE,
        OutOfMemory,
        Internal, // any other std::exception
        Unknown,  // not derived from std::exception
    };

    struct ErrorInfo
    {
        ErrorKind kind{ErrorKind::Unknown};
        std::string message{};
    };

    // Must be called from inside a catch block.
    inline auto classify_current_exception() -> ErrorInfo
    {
        try
        {
            throw;
        }
        catch (const PythonErrorSet&)
        {
            return {ErrorKind::PythonErrorSet, {}};
        }
        catch (const InvalidObjectError& e)
        {
            return {ErrorKind::InvalidObject, e.what()};
        }
        catch (const PropertyNotFoundError& e)
        {
            return {ErrorKind::PropertyNotFound, e.what()};
        }
        catch (const UEError& e)
        {
            return {ErrorKind::UE, e.what()};
        }
        catch (const std::bad_alloc&)
        {
            return {ErrorKind::OutOfMemory, "out of memory"};
        }
        catch (const std::exception& e)
        {
            return {ErrorKind::Internal, e.what()};
        }
        catch (...)
        {
            return {ErrorKind::Unknown, "unknown C++ exception"};
        }
    }
}
