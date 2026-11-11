"""Find out which game function a key press calls (ticket 58j.23; owner test 2026-10-28: T still opened the team chat with the input box open, F1 still opened the key list).

    from ue4ss import trace
    session = trace.start([controller.get_class()], r"chat|say|keymap|toggle", seconds=8)    # logs "[trace] Class:Function" once per function that is called
    session.stop()

Hooks (ue4ss.register_hook) are put on every function of the given classes and their parents whose name matches the pattern (noisy per-frame names are skipped) and removed after `seconds`. The log lines
tell which function to replace to block the key. Needs the game thread.
"""
import re

import ue4ss

NOISE = re.compile(r"tick|axis|move|look|^get|^is|^can|^has|tooltip|brush|mouse|cursor|camera|anim|sound|audio|^k2_get|^on_?rep", re.I)
MAX_HOOKS = 120


class TraceSession:
    def __init__(self, log=print):
        self.log = log
        self.ids = []
        self.seen = set()

    def stop(self):
        for ids in self.ids:
            try:
                ue4ss.unregister_hook(*ids)
            except Exception:  # noqa: BLE001
                pass
        self.ids = []


def start(classes, pattern, seconds=8.0, log=print, schedule=None):
    session = TraceSession(log)
    wanted = re.compile(pattern, re.I)
    done = set()
    for cls in classes:
        while cls is not None and cls.get_fname().to_string() not in ("Object", "Actor"):
            class_name = cls.get_fname().to_string()
            for name in cls.get_function_names():
                if len(session.ids) >= MAX_HOOKS or (class_name, name) in done or not wanted.search(name) or NOISE.search(name):
                    continue
                done.add((class_name, name))
                function = cls.find_function(name)
                if function is None:
                    continue
                path = function.get_full_name().split(" ", 1)[1]

                def pre(context, params, label=f"{class_name}:{name}"):
                    if label not in session.seen:
                        session.seen.add(label)
                        log(f"[trace] {label}")
                try:
                    session.ids.append(ue4ss.register_hook(path, pre=pre))
                except Exception:  # noqa: BLE001 - a function that cannot be hooked is skipped
                    pass
            cls = cls.get_super()
    log(f"[trace] watching {len(session.ids)} functions for {seconds:.0f} s")
    (schedule or ue4ss.execute_with_delay)(int(seconds * 1000), session.stop)
    return session
