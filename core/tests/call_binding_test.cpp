#include "test.hpp"

#include <pyue/call_binding.hpp>

using namespace pyue;

static ParamSpec input(const char* name) { return ParamSpec{name, false, false, false, false}; }
static ParamSpec output(const char* name) { return ParamSpec{name, true, false, false, false}; }
static ParamSpec ret(const char* name = "ReturnValue") { return ParamSpec{name, true, true, false, false}; }
static ParamSpec const_ref(const char* name) { return ParamSpec{name, true, false, true, true}; }  // const FVector&
static ParamSpec ref(const char* name) { return ParamSpec{name, true, false, false, true}; }       // FVector& (in/out)

int main()
{
    {
        // non-const reference parameters are inputs that are also returned, after the outputs
        const std::vector<ParamSpec> specs = {{"Stream", true, false, false, true}, {"Seed", false, false, false, false}, {"Offset", true, false, true, true},
                                              {"Out", true, false, false, false}, {"ReturnValue", true, true, false, false}, {"Other", true, false, false, true}};
        PYUE_CHECK(is_in_out(specs[0]));
        PYUE_CHECK(!is_in_out(specs[1]));
        PYUE_CHECK(!is_in_out(specs[2])); // const reference: input only
        PYUE_CHECK(!is_in_out(specs[3])); // plain output
        PYUE_CHECK(!is_in_out(specs[4]));
        PYUE_CHECK(is_in_out(specs[5]));
        PYUE_CHECK(role_of(specs[0]) == ParamRole::Input);
        const auto plan = plan_results(specs);
        PYUE_CHECK((plan == std::vector<std::size_t>{4, 3, 0, 5})); // return, output, then the in/outs
        const auto bound = bind_arguments(specs, 4, {});
        PYUE_CHECK(bound.ok); // Stream, Seed, Offset and Other are the inputs; Out and ReturnValue are not passed
        PYUE_CHECK(bound.assignments.size() == 4);
    }
    {
        // Roles. A const reference is an input even though Unreal sets the out flag on it.
        PYUE_CHECK(role_of(input("A")) == ParamRole::Input);
        PYUE_CHECK(role_of(output("A")) == ParamRole::Output);
        PYUE_CHECK(role_of(ret()) == ParamRole::Return);
        PYUE_CHECK(role_of(const_ref("A")) == ParamRole::Input);
        PYUE_CHECK(role_of(ref("A")) == ParamRole::Input);
        // The return flag wins over everything else.
        PYUE_CHECK(role_of(ParamSpec{"R", true, true, true, true}) == ParamRole::Return);
    }
    {
        // K2_SetActorLocation(NewLocation, bSweep, out SweepHitResult, bTeleport) -> bool
        const std::vector<ParamSpec> specs = {input("NewLocation"), input("bSweep"), output("SweepHitResult"), input("bTeleport"), ret()};

        auto r = bind_arguments(specs, 3, {});
        PYUE_CHECK(r.ok);
        PYUE_CHECK(r.assignments.size() == 3);
        PYUE_CHECK(r.assignments[0].param == 0 && r.assignments[1].param == 1);
        PYUE_CHECK(r.assignments[2].param == 3); // the third positional skips the output
        PYUE_CHECK(r.assignments[2].source == Assignment::Source::Positional && r.assignments[2].index == 2);

        r = bind_arguments(specs, 1, {"bSweep", "bTeleport"});
        PYUE_CHECK(r.ok && r.assignments.size() == 3);
        PYUE_CHECK(r.assignments[1].param == 1 && r.assignments[1].source == Assignment::Source::Keyword && r.assignments[1].index == 0);
        PYUE_CHECK(r.assignments[2].param == 3 && r.assignments[2].index == 1);

        r = bind_arguments(specs, 0, {"bteleport", "NEWLOCATION", "bsweep"}); // case-insensitive, any order
        PYUE_CHECK(r.ok && r.assignments.size() == 3);

        const auto plan = plan_results(specs);
        PYUE_CHECK(plan.size() == 2 && plan[0] == 4 && plan[1] == 2); // return value first, then the output
    }
    {
        const std::vector<ParamSpec> specs = {input("A"), input("B")};

        auto r = bind_arguments(specs, 3, {});
        PYUE_CHECK(!r.ok && r.error == "takes 2 positional arguments but 3 were given");
        r = bind_arguments(specs, 1, {});
        PYUE_CHECK(!r.ok && r.error == "missing 1 required argument: 'B'");
        r = bind_arguments(specs, 0, {});
        PYUE_CHECK(!r.ok && r.error == "missing 2 required arguments: 'A', 'B'");
        r = bind_arguments(specs, 1, {"A"});
        PYUE_CHECK(!r.ok && r.error == "got multiple values for argument 'A'");
        r = bind_arguments(specs, 1, {"C"});
        PYUE_CHECK(!r.ok && r.error == "got an unexpected keyword argument 'C'");
        PYUE_CHECK(r.assignments.empty()); // nothing half-bound on failure
        PYUE_CHECK(bind_arguments(specs, 2, {}).ok);
        PYUE_CHECK(plan_results(specs).empty());
    }
    {
        // Outputs and the return value cannot be passed.
        const std::vector<ParamSpec> specs = {input("In"), output("Out"), ret()};
        auto r = bind_arguments(specs, 0, {"In", "Out"});
        PYUE_CHECK(!r.ok && r.error == "'Out' is an output of the function and cannot be passed");
        r = bind_arguments(specs, 0, {"In", "ReturnValue"});
        PYUE_CHECK(!r.ok && r.error == "'ReturnValue' is an output of the function and cannot be passed");
        r = bind_arguments(specs, 2, {});
        PYUE_CHECK(!r.ok && r.error == "takes 1 positional argument but 2 were given");
    }
    {
        // No parameters at all, and a function that only returns.
        PYUE_CHECK(bind_arguments({}, 0, {}).ok);
        PYUE_CHECK(plan_results({}).empty());
        const std::vector<ParamSpec> only_return = {ret()};
        PYUE_CHECK(bind_arguments(only_return, 0, {}).ok);
        PYUE_CHECK(plan_results(only_return).size() == 1);
        auto r = bind_arguments(only_return, 1, {});
        PYUE_CHECK(!r.ok && r.error == "takes 0 positional arguments but 1 was given");
    }
    {
        // By-reference parameters are inputs: they are passed; a non-const one is also returned, after the outputs.
        const std::vector<ParamSpec> specs = {const_ref("Offset"), ref("InOut"), output("Result")};
        auto r = bind_arguments(specs, 2, {});
        PYUE_CHECK(r.ok && r.assignments.size() == 2 && r.assignments[0].param == 0 && r.assignments[1].param == 1);
        const auto plan = plan_results(specs);
        PYUE_CHECK(plan.size() == 2 && plan[0] == 2 && plan[1] == 1); // the pure output, then the in/out; the const reference is not returned
    }
    return PYUE_TEST_MAIN_RESULT();
}
