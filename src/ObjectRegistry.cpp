#include "ObjectRegistry.hpp"
#include "ThreadProbe.hpp"

#include <atomic>
#include <cstdint>

#include <excpt.h>

#include <Unreal/UObject.hpp>
#include <Unreal/UObjectArray.hpp>
#include <Unreal/UObjectGlobals.hpp>

#include <pyue/errors.hpp>

#include "GameThreadExecutor.hpp"
#include "SafeCall.hpp"

using namespace RC;

namespace pyue::objects
{
    namespace
    {
        pyue::HandleTable s_table{};
        std::atomic<bool> s_installed{false};
        std::atomic<std::uint64_t> s_deleted_total{0};
        std::atomic<std::uint64_t> s_deleted_on_game_thread{0};
        std::atomic<std::uint64_t> s_deleted_off_game_thread{0};
        std::atomic<std::uint64_t> s_deleted_before_known{0};

        // Runs wherever the engine frees an object, possibly inside engine locks: it may only touch
        // the table and the counters (no GIL, no Python, no engine calls). See docs/LIFETIME.md rule 1.
        class DeleteListener final : public Unreal::FUObjectDeleteListener
        {
        public:
            void NotifyUObjectDeleted(const Unreal::UObjectBase*, int32_t Index) override
            {
                s_table.on_deleted(Index);
                ++s_deleted_total;
                thread_probe::record(thread_probe::Site::ObjectDeleted);
                if (game_thread_executor::on_game_thread())
                {
                    ++s_deleted_on_game_thread;
                }
                else if (game_thread_executor::game_thread_known())
                {
                    ++s_deleted_off_game_thread;
                }
                else
                {
                    ++s_deleted_before_known;
                }
            }

            void OnUObjectArrayShutdown() override
            {
                s_table.shutdown();
                s_installed.store(false);
                Unreal::UObjectArray::RemoveUObjectDeleteListener(this);
            }
        };

        DeleteListener s_listener{};

        // Reads the object's index; false when the address cannot be read. A function of its own because __try cannot share a
        // function with objects that have destructors.
        auto read_index_guarded(const Unreal::UObject* object, std::int32_t& index) -> bool
        {
            __try
            {
                // open the plugin read-guard here too. Frame SEH does not recover this
                // fault class seen in one game (docs/FAULTS.md): without the seal a poisoned read escapes this
                // __except and kills the game. The VEH seals the read (zeroed register, no exception), so a
                // seal counts the read as failed instead of trusting the garbage index it left behind.
                begin_read_guard();
                index = object->GetInternalIndex();
                end_read_guard();
                return take_sealed_read() == 0;
            }
            __except (EXCEPTION_EXECUTE_HANDLER)
            {
                // Balance the per-thread depth: MSVC __except does not run the rest of the __try. Nothing can
                // be pending here (a sealed fault never raises), so there is no seal to consume.
                end_read_guard();
                return false;
            }
        }

        // nullptr when the reference is usable, otherwise why not.
        auto invalid_reason(const ObjectRef& ref) -> const char*
        {
            if (!ref.object || ref.handle.index < 0) return "the object reference is null";
            if (!s_table.is_current(ref.handle)) return "the object was destroyed";

            const Unreal::FUObjectItem* item = Unreal::UObjectArray::IndexToObject(ref.handle.index);
            if (!item || item->GetUObject() != ref.object) return "the object's slot was reused by another object";
            if (item->IsUnreachable()) return "the object is unreachable (being garbage collected)";
            if (item->IsPendingKill()) return "the object is pending kill";
            return nullptr;
        }
    }

    auto install() -> bool
    {
        if (s_installed.exchange(true)) return true;
        Unreal::UObjectArray::AddUObjectDeleteListener(&s_listener);
        return true;
    }

    auto uninstall() -> void
    {
        if (!s_installed.exchange(false)) return;
        try
        {
            Unreal::UObjectArray::RemoveUObjectDeleteListener(&s_listener);
        }
        catch (...)
        {
            // The object array may already be gone; the listener is inert once s_installed is false.
        }
        s_table.shutdown(); // every handle is stale from now on
    }

    auto acquire(Unreal::UObject* object) -> ObjectRef
    {
        if (!s_installed.load()) throw UEError("the object registry is not installed");
        if (!object) return ObjectRef{};
        return ObjectRef{s_table.acquire(object->GetInternalIndex()), object};
    }

    auto acquire_untrusted(Unreal::UObject* object) -> ObjectRef
    {
        if (!s_installed.load()) throw UEError("the object registry is not installed");
        const auto address = reinterpret_cast<std::uintptr_t>(object);
        if (address < 0x10000 || (address & 7) != 0) return ObjectRef{}; // null, tiny integers and misaligned values are not objects

        std::int32_t index = -1;
        if (!read_index_guarded(object, index)) return ObjectRef{}; // unmapped or protected memory

        // The slot the pointer names must hold exactly this object; freed memory, interior pointers and random data fail here.
        const Unreal::FUObjectItem* item = Unreal::UObjectArray::IndexToObject(index);
        if (!item || item->GetUObject() != object) return ObjectRef{};
        return ObjectRef{s_table.acquire(index), object};
    }

    auto resolve(const ObjectRef& ref) -> Unreal::UObject*
    {
        if (const char* reason = invalid_reason(ref)) throw InvalidObjectError(reason);
        return ref.object;
    }

    auto is_valid(const ObjectRef& ref) -> bool
    {
        return invalid_reason(ref) == nullptr;
    }

    auto stats() -> Stats
    {
        return Stats{s_deleted_total.load(), s_deleted_on_game_thread.load(), s_deleted_off_game_thread.load(), s_deleted_before_known.load()};
    }

    auto run_self_test() -> SelfTestResult
    {
        SelfTestResult result;

        auto* object = Unreal::UObjectGlobals::StaticFindObject<Unreal::UObject*>(nullptr, nullptr, STR("/Script/CoreUObject.Object"));
        if (!object) return result;
        result.object_name = std::wstring(object->GetFullName());

        const ObjectRef ref = acquire(object);
        result.resolved = is_valid(ref) && resolve(ref) == object;

        // A handle from an earlier generation of the same slot (made without touching the real table).
        ObjectRef stale = ref;
        stale.handle.generation += 1;
        result.stale_rejected = !is_valid(stale);

        // Right handle, but the slot holds a different object now.
        ObjectRef other = ref;
        other.object = reinterpret_cast<Unreal::UObject*>(reinterpret_cast<std::uintptr_t>(object) + 0x10);
        result.slot_mismatch_rejected = !is_valid(other);

        // Index beyond the object array, built by hand so the table is not asked to grow.
        ObjectRef out_of_range = ref;
        out_of_range.handle.index = 0x7fffffff;
        result.out_of_range_rejected = !is_valid(out_of_range);

        result.null_rejected = !is_valid(ObjectRef{});

        try
        {
            (void)resolve(stale);
        }
        catch (const InvalidObjectError& e)
        {
            result.destroyed_message_ok = std::string_view(e.what()).find("destroyed") != std::string_view::npos;
        }

        result.stats = stats();
        return result;
    }
}
