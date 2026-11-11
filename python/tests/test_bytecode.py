"""ue4ss.bytecode: the Blueprint bytecode walker, on hand-made byte sequences."""
import pathlib
import struct
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python" / "mock"))
import ue4ss  # noqa: E402
from ue4ss import bytecode  # noqa: E402

END = b"\x53"
ENDPARMS = b"\x16"


def ptr(value):
    return struct.pack("<Q", value)


def name(index, number=0, size=8):
    if size == 12:
        return struct.pack("<iiI", index, index, number)
    return struct.pack("<ii", index, number)


def intc(value):
    return b"\x1d" + struct.pack("<i", value)


class WalkerTest(unittest.TestCase):
    def test_the_last_two_tokens_of_engine_4_25_parse(self):
        # ClassSparseDataVariable carries a property pointer like the other variable tokens; FieldPathConst wraps one expression
        script = b"\x0f" + ptr(0xAA) + b"\x6c" + ptr(0x5150) + b"\x6d" + b"\x1f" + b"Prop\x00" + END      # Let(prop, SparseVar, FieldPathConst("Prop"))
        nodes = bytecode.parse(script)
        self.assertEqual([n.name for n in nodes], ["Let", "EndOfScript"])
        names = []

        def walk(node):
            names.append(node.name)
            for child in getattr(node, "children", []) or []:
                walk(child)

        for node in nodes:
            walk(node)
        self.assertIn("ClassSparseDataVariable", names)
        self.assertIn("FieldPathConst", names)
        self.assertIn("StringConst", names)

    def test_the_token_table_covers_every_token_of_engine_4_25_up_to_its_last(self):
        self.assertEqual(max(bytecode._TOKENS), 0x6D)

    def test_end_of_script_alone(self):
        nodes = bytecode.parse(END)
        self.assertEqual([n.name for n in nodes], ["EndOfScript"])

    def test_final_function_call_with_parameters(self):
        script = b"\x46" + ptr(0x1234) + intc(5) + b"\x27" + ENDPARMS + END    # LocalFinalFunction(target, 5, True)
        nodes = bytecode.parse(script)
        calls = bytecode.calls_in(nodes)
        self.assertEqual(len(calls), 1)
        self.assertEqual((calls[0]["kind"], calls[0]["local"], calls[0]["pointer"], calls[0]["offset"]), ("final", True, 0x1234, 0))
        self.assertEqual([c.name for c in nodes[0].children], ["IntConst", "True", "EndFunctionParms"])

    def test_nested_calls_are_found_inside_other_expressions(self):
        inner = b"\x68" + ptr(0x99) + intc(1) + ENDPARMS                           # CallMath(0x99, 1)
        script = b"\x0f" + ptr(0x10) + b"\x00" + ptr(0x20) + b"\x1c" + ptr(0x55) + inner + ENDPARMS + END   # Let(var, local, FinalFunction(0x55, CallMath(...)))
        calls = bytecode.calls_in(bytecode.parse(script))
        self.assertEqual([(c["kind"], c["pointer"]) for c in calls], [("final", 0x55), ("math", 0x99)])

    def test_virtual_call_carries_a_name(self):
        script = b"\x45" + name(77, 3) + ENDPARMS + END
        call = bytecode.calls_in(bytecode.parse(script))[0]
        self.assertEqual((call["kind"], call["local"], call["name"]), ("virtual", True, (77, 3)))

    def test_context_and_jumps_and_strings(self):
        script = (b"\x19" + b"\x17" + struct.pack("<I", 4) + ptr(0x30) + b"\x1b" + name(5) + ENDPARMS      # Context(Self, skip, prop, VirtualFunction)
                  + b"\x07" + struct.pack("<I", 100) + b"\x28"                                               # JumpIfNot(100, False)
                  + b"\x1f" + b"hello\0" + b"\x34" + "wide".encode("utf-16-le") + b"\0\0" + END)
        nodes = bytecode.parse(script)
        self.assertEqual([n.name for n in nodes], ["Context", "JumpIfNot", "StringConst", "UnicodeStringConst", "EndOfScript"])
        self.assertEqual(nodes[2].values, [("str", "hello")])
        self.assertEqual(nodes[3].values, [("str", "wide")])
        self.assertEqual(len(bytecode.calls_in(nodes)), 1)

    def test_switch_text_and_struct_constants(self):
        switch = b"\x69" + struct.pack("<HI", 2, 0) + b"\x1d" + struct.pack("<i", 1)
        switch += intc(10) + struct.pack("<I", 0) + intc(100) + intc(20) + struct.pack("<I", 0) + intc(200) + intc(0)
        text = b"\x29\x03" + b"\x1f" + b"lit\0"                                                              # TextConst literal string
        struct_const = b"\x2f" + ptr(0x40) + struct.pack("<i", 8) + intc(1) + b"\x30"
        nodes = bytecode.parse(switch + text + struct_const + END)
        self.assertEqual([n.name for n in nodes], ["SwitchValue", "TextConst", "StructConst", "EndOfScript"])

    def test_names_of_twelve_bytes_are_detected(self):
        script = b"\x1b" + name(9, 1, size=12) + ENDPARMS + END
        with self.assertRaises(bytecode.BytecodeError):
            bytecode.parse(script, 8)
        nodes, size = bytecode.parse_auto(script)
        self.assertEqual(size, 12)
        self.assertEqual(bytecode.calls_in(nodes)[0]["name"], (9, 1))

    def test_malformed_streams_are_refused(self):
        for label, script in (("empty", b""), ("no end", intc(1)), ("unknown token", b"\xfe" + END), ("truncated operand", b"\x1d\x01"),
                              ("trailing bytes", END + END), ("unterminated string", b"\x1f" + b"abc"),
                              ("unterminated params", b"\x1c" + ptr(1) + intc(1))):
            with self.subTest(label):
                with self.assertRaises(bytecode.BytecodeError):
                    bytecode.parse(script)

    def test_deep_nesting_is_refused(self):
        script = b"\x04" * 500 + b"\x0b" + END
        with self.assertRaises(bytecode.BytecodeError):
            bytecode.parse(script)


class ScanCallsTest(unittest.TestCase):
    def setUp(self):
        ue4ss._mock_reset_objects()
        ue4ss._mock_define_class("/Game/Foo.Foo_C", "/Script/CoreUObject.Object")

    def tearDown(self):
        ue4ss._mock_reset_objects()

    def test_scan_resolves_pointers_to_function_paths(self):
        target = ue4ss._mock_define_function("/Game/Foo.Foo_C", "Helper", flags=0, impl=lambda block: None)
        script = b"\x46" + ptr(target.get_address()) + ENDPARMS + b"\x46" + ptr(0x7777) + ENDPARMS + END
        func = ue4ss._mock_define_function("/Game/Foo.Foo_C", "Main", flags=0, impl=lambda block: None, script=script)
        calls = bytecode.scan_calls(func)
        self.assertEqual([c["target"] for c in calls], ["/Game/Foo.Foo_C:Helper", None])
        self.assertTrue(all(c["local"] for c in calls))

    def test_a_native_function_has_no_calls(self):
        func = ue4ss._mock_define_function("/Game/Foo.Foo_C", "Native", flags=0x400, impl=lambda block: None)
        self.assertEqual(func.get_script(), b"")
        self.assertEqual(bytecode.scan_calls(func), [])

    def test_object_at_address(self):
        obj = ue4ss._mock_define_function("/Game/Foo.Foo_C", "A", impl=lambda block: None)
        self.assertEqual(ue4ss.object_at_address(obj.get_address()), obj)
        self.assertIsNone(ue4ss.object_at_address(0x1))
        self.assertIsNone(ue4ss.object_at_address(2**64 - 1))
        with self.assertRaises(TypeError):
            ue4ss.object_at_address("1")
        with self.assertRaises(OverflowError):
            ue4ss.object_at_address(-1)


if __name__ == "__main__":
    unittest.main()
