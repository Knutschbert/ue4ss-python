# Copy over python_scripts/main.py to check the object registry in the game. Prints PASS/FAIL per check.
import ue4ss


def check(label, ok, detail=""):
    print(f"{label}: {'PASS' if ok else 'FAIL'}{(' (' + detail + ')') if detail else ''}")


result = ue4ss._selftest_objects()
for key, value in result.items():
    if isinstance(value, bool):
        check(f"registry self test {key}", value)
print("object checked:", result["object_name"])


def report():
    stats = ue4ss._selftest_objects()
    total = stats["deleted_total"]
    parts = stats["deleted_on_game_thread"] + stats["deleted_off_game_thread"] + stats["deleted_before_game_thread_known"]
    print("deletions so far:", total, "| on game thread:", stats["deleted_on_game_thread"], "| off:", stats["deleted_off_game_thread"],
          "| before the game thread was known:", stats["deleted_before_game_thread_known"])
    check("deletion counters add up", total >= parts and total - parts <= 64, f"{total} vs {parts}")
    print("objects example finished")


ue4ss.execute_with_delay(3000, report)
