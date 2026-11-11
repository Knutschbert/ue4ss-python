#pragma once

#include <cctype>
#include <cstddef>
#include <string>
#include <vector>

// Binding Python call arguments to the parameters of an Unreal function, free of Unreal and Python so the rules can be
// tested exhaustively. Unreal marks parameters with flags; this turns them into roles:
//
//   Return - the function's return value (never passed)
//   Output - an out parameter the function fills in (never passed; returned to the caller)
//   Input  - everything else, including by-reference and const-reference parameters (passed by the caller)
//
// In Unreal a const reference parameter (const FVector&) carries the out and reference flags too but is an input;
// only "out and not reference" is a pure output. A non-const reference parameter (UPARAM(ref) FRandomStream&) is an input
// that the function may also change: it is passed by the caller AND its updated value is returned (is_in_out()).
namespace pyue
{
    struct ParamSpec
    {
        std::string name{};
        bool is_out{false};
        bool is_return{false};
        bool is_const{false};
        bool is_reference{false};
    };

    enum class ParamRole
    {
        Input,
        Output,
        Return,
    };

    inline auto role_of(const ParamSpec& spec) -> ParamRole
    {
        if (spec.is_return) return ParamRole::Return;
        if (spec.is_out && !spec.is_reference) return ParamRole::Output;
        return ParamRole::Input;
    }

    // A non-const by-reference parameter: the function reads it and may write it back.
    inline auto is_in_out(const ParamSpec& spec) -> bool
    {
        return !spec.is_return && spec.is_out && spec.is_reference && !spec.is_const;
    }

    struct Assignment
    {
        enum class Source
        {
            Positional,
            Keyword
        };

        std::size_t param{0};  // index into the parameter list
        Source source{Source::Positional};
        std::size_t index{0};  // index among the positional arguments, or among the keyword names
    };

    struct BindResult
    {
        bool ok{false};
        std::string error{};
        std::vector<Assignment> assignments{};
    };

    namespace detail
    {
        inline auto lower_ascii(std::string text) -> std::string
        {
            for (auto& c : text) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
            return text;
        }

        inline auto plural(std::size_t count, const char* singular, const char* many) -> std::string
        {
            return std::to_string(count) + " " + (count == 1 ? singular : many);
        }
    }

    // Parameter names are case-insensitive, like every Unreal name.
    inline auto bind_arguments(const std::vector<ParamSpec>& specs, std::size_t positional_count, const std::vector<std::string>& keywords) -> BindResult
    {
        BindResult result;

        std::vector<std::size_t> inputs;
        for (std::size_t i = 0; i < specs.size(); ++i)
        {
            if (role_of(specs[i]) == ParamRole::Input) inputs.push_back(i);
        }

        if (positional_count > inputs.size())
        {
            result.error = "takes " + detail::plural(inputs.size(), "positional argument", "positional arguments") + " but " + std::to_string(positional_count) + (positional_count == 1 ? " was" : " were") + " given";
            return result;
        }

        std::vector<bool> filled(specs.size(), false);
        for (std::size_t p = 0; p < positional_count; ++p)
        {
            result.assignments.push_back({inputs[p], Assignment::Source::Positional, p});
            filled[inputs[p]] = true;
        }

        for (std::size_t k = 0; k < keywords.size(); ++k)
        {
            const std::string wanted = detail::lower_ascii(keywords[k]);
            std::size_t found = specs.size();
            for (std::size_t i = 0; i < specs.size(); ++i)
            {
                if (detail::lower_ascii(specs[i].name) == wanted)
                {
                    found = i;
                    break;
                }
            }
            if (found == specs.size())
            {
                result.error = "got an unexpected keyword argument '" + keywords[k] + "'";
                result.assignments.clear();
                return result;
            }
            if (role_of(specs[found]) != ParamRole::Input)
            {
                result.error = "'" + keywords[k] + "' is an output of the function and cannot be passed";
                result.assignments.clear();
                return result;
            }
            if (filled[found])
            {
                result.error = "got multiple values for argument '" + keywords[k] + "'";
                result.assignments.clear();
                return result;
            }
            result.assignments.push_back({found, Assignment::Source::Keyword, k});
            filled[found] = true;
        }

        std::vector<std::string> missing;
        for (const std::size_t i : inputs)
        {
            if (!filled[i]) missing.push_back("'" + specs[i].name + "'");
        }
        if (!missing.empty())
        {
            result.error = "missing " + detail::plural(missing.size(), "required argument", "required arguments") + ": ";
            for (std::size_t m = 0; m < missing.size(); ++m) result.error += (m ? ", " : "") + missing[m];
            result.assignments.clear();
            return result;
        }

        result.ok = true;
        return result;
    }

    // Which parameters the call returns, in order: the return value first, then the outputs in declaration order, then the
    // updated value of every in/out (non-const reference) parameter in declaration order.
    // No entries means the call returns None, one entry means that value alone, more means a tuple.
    inline auto plan_results(const std::vector<ParamSpec>& specs) -> std::vector<std::size_t>
    {
        std::vector<std::size_t> plan;
        for (std::size_t i = 0; i < specs.size(); ++i)
        {
            if (role_of(specs[i]) == ParamRole::Return) plan.push_back(i);
        }
        for (std::size_t i = 0; i < specs.size(); ++i)
        {
            if (role_of(specs[i]) == ParamRole::Output) plan.push_back(i);
        }
        for (std::size_t i = 0; i < specs.size(); ++i)
        {
            if (is_in_out(specs[i])) plan.push_back(i);
        }
        return plan;
    }
}
