#include <filesystem>
#include <string>

#include <Mod/CppUserModBase.hpp>
#include <DynamicOutput/DynamicOutput.hpp>

#include <pyue/entry_script.hpp>
#include <pyue/runtime_config.hpp>
#include <pyue/version.hpp>

#include "ThreadProbe.hpp"
#include "GameThreadExecutor.hpp"
#include "PyHooks.hpp"
#include "PyKeys.hpp"
#include "Reload.hpp"
#include "PyNewObjects.hpp"
#include "ObjectRegistry.hpp"
#include "PythonRuntime.hpp"

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <Windows.h>

using namespace RC;

namespace
{
    auto widen(const std::string& utf8) -> std::wstring
    {
        if (utf8.empty()) return {};
        const int size = MultiByteToWideChar(CP_UTF8, 0, utf8.data(), static_cast<int>(utf8.size()), nullptr, 0);
        std::wstring result(static_cast<size_t>(size), L'\0');
        MultiByteToWideChar(CP_UTF8, 0, utf8.data(), static_cast<int>(utf8.size()), result.data(), size);
        return result;
    }

    // Directory containing this DLL, i.e. <mod>/dlls (UE4SS loads <mod>/dlls/main.dll).
    auto this_dll_directory() -> std::filesystem::path
    {
        HMODULE module{};
        if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                                reinterpret_cast<LPCWSTR>(&this_dll_directory),
                                &module))
        {
            return {};
        }
        std::wstring buffer(MAX_PATH, L'\0');
        for (;;)
        {
            const DWORD length = GetModuleFileNameW(module, buffer.data(), static_cast<DWORD>(buffer.size()));
            if (length == 0) return {};
            if (length < buffer.size())
            {
                buffer.resize(length);
                break;
            }
            buffer.resize(buffer.size() * 2);
        }
        return std::filesystem::path(buffer).parent_path();
    }
}

class PythonScriptingMod : public CppUserModBase
{
private:
    pyue::PythonRuntime m_python{};

    auto start_python() -> void
    {
        try
        {
            start_python_impl();
        }
        catch (const std::exception& e)
        {
            Output::send<LogLevel::Error>(STR("PythonScripting: exception while starting Python: {}\n"), widen(e.what()));
        }
        catch (...)
        {
            Output::send<LogLevel::Error>(STR("PythonScripting: unknown exception while starting Python\n"));
        }
    }

    auto start_game_thread_executor() -> void
    {
        namespace executor = pyue::game_thread_executor;
        switch (executor::install())
        {
        case executor::Method::EngineTick:
            Output::send<LogLevel::Verbose>(STR("PythonScripting: game-thread executor installed (EngineTick)\n"));
            break;
        case executor::Method::ProcessEvent:
            Output::send<LogLevel::Warning>(STR("PythonScripting: game-thread executor installed (ProcessEvent fallback, no EngineTick)\n"));
            break;
        case executor::Method::None:
            Output::send<LogLevel::Error>(STR("PythonScripting: no game-thread hook available, execute_in_game_thread will not work\n"));
            return;
        }

        // Proof that queued work runs on the game thread (checked in the log).
        executor::post([] {
            Output::send<LogLevel::Verbose>(STR("PythonScripting: executor test task ran (on_game_thread={})\n"), executor::on_game_thread());
        });
    }

    auto start_python_impl() -> void
    {
        start_game_thread_executor();

        if (pyue::objects::install())
        {
            Output::send<LogLevel::Verbose>(STR("PythonScripting: object registry installed\n"));
        }

        const auto dlls_dir = this_dll_directory();
        if (dlls_dir.empty())
        {
            Output::send<LogLevel::Error>(STR("PythonScripting: could not determine the mod's dlls directory\n"));
            return;
        }

        m_python.set_log_sink([](bool is_error, const std::string& line) {
            if (is_error)
            {
                Output::send<LogLevel::Error>(STR("[Python] {}\n"), widen(line));
            }
            else
            {
                Output::send<LogLevel::Normal>(STR("[Python] {}\n"), widen(line));
            }
        });

        const auto config = pyue::make_runtime_config(dlls_dir, pyue::default_scripts_dir(dlls_dir), "python312.zip");
        if (!m_python.start(config))
        {
            Output::send<LogLevel::Error>(STR("PythonScripting: failed to start Python: {}\n"), widen(m_python.last_error()));
            return;
        }

        Output::send<LogLevel::Verbose>(STR("PythonScripting: Python {}\n"), widen(m_python.python_version()));
        if (!m_python.self_check())
        {
            Output::send<LogLevel::Error>(STR("PythonScripting: Python self check failed: {}\n"), widen(m_python.last_error()));
            return;
        }
        Output::send<LogLevel::Verbose>(STR("PythonScripting: Python self check passed\n"));

        // From here on every entry into Python takes the GIL with a GilGuard.
        m_python.release_gil();
        Output::send<LogLevel::Verbose>(STR("PythonScripting: GIL released\n"));

        const auto entry = pyue::resolve_entry_script(config);
        if (!entry)
        {
            Output::send<LogLevel::Verbose>(STR("PythonScripting: no main.py in {}, nothing to run\n"), config.scripts_dir.wstring());
            return;
        }

        // Reload (ue4ss.reload) runs the same entry script again.
        pyue::reload::configure(config.scripts_dir.wstring(), [this, script = *entry](std::string& error) {
            const bool ok = m_python.run_file(script);
            if (!ok) error = m_python.last_error();
            return ok;
        });

        auto run_entry = [this, script = *entry] {
            pyue::reload::mark_started();
            Output::send<LogLevel::Verbose>(STR("PythonScripting: running {} (on_game_thread={})\n"),
                                            script.wstring(),
                                            pyue::game_thread_executor::on_game_thread());
            if (m_python.run_file(script))
            {
                Output::send<LogLevel::Verbose>(STR("PythonScripting: main.py finished\n"));
            }
            else
            {
                Output::send<LogLevel::Error>(STR("PythonScripting: main.py failed: {}\n"), widen(m_python.last_error()));
            }
        };

        // main.py may touch the game, so it runs on the game thread, from the first engine tick.
        if (pyue::game_thread_executor::method() != pyue::game_thread_executor::Method::None)
        {
            Output::send<LogLevel::Verbose>(STR("PythonScripting: main.py queued for the first game-thread tick\n"));
            pyue::game_thread_executor::post(std::move(run_entry));
        }
        else
        {
            Output::send<LogLevel::Warning>(STR("PythonScripting: no game-thread hook, running main.py on the UE4SS thread\n"));
            run_entry();
        }
    }

public:
    PythonScriptingMod() : CppUserModBase()
    {
        ModName = STR("PythonScripting");
        ModVersion = STR("0.1");
        ModDescription = STR("Python scripting layer for UE4SS");
        ModAuthors = STR("Knutschbert");

        register_on_unreal_init([](CppUserModBase* mod) {
            pyue::thread_probe::record(pyue::thread_probe::Site::Init);
            Output::send<LogLevel::Verbose>(STR("PythonScripting: Unreal initialised\n"));
            static_cast<PythonScriptingMod*>(mod)->start_python();
        });

        Output::send<LogLevel::Verbose>(STR("PythonScripting {}.{} says hello\n"), pyue::version_major, pyue::version_minor);
    }

    ~PythonScriptingMod() override
    {
        Output::send<LogLevel::Verbose>(STR("PythonScripting: shutting down\n"));
        pyue::unregister_all_hooks();            // best effort; the trampolines are inert once Python is down
        pyue::unregister_all_new_object_notifications();
        pyue::unregister_all_key_bindings();
        pyue::game_thread_executor::uninstall(); // before m_python goes away: no more drains
        pyue::objects::uninstall();
        // m_python finalizes the interpreter
        Output::send<LogLevel::Verbose>(STR("PythonScripting: native parts removed, finalizing Python\n"));
    }
};

#define PYTHON_SCRIPTING_MOD_API __declspec(dllexport)
extern "C"
{
    PYTHON_SCRIPTING_MOD_API RC::CppUserModBase* start_mod()
    {
        return new PythonScriptingMod();
    }

    PYTHON_SCRIPTING_MOD_API void uninstall_mod(RC::CppUserModBase* mod)
    {
        delete mod;
    }
}
