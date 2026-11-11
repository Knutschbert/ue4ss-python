# Engine names that are not Python identifiers ("Sun height") work under the identifier form the typings declare ("Sun_height").
# Test game: the variables of the engine Blueprint BP_Sky_Sphere. Chivalry 2: a Blueprint function with spaces in its name (OverviewScreen_C).
import ue4ss


def check(label, ok, detail=""):
    print(f"{label}: {'PASS' if ok else 'FAIL'}{(' (' + str(detail)[:300] + ')') if detail else ''}")


def skip(label, why):
    print(f"{label}: SKIP ({why})")


def raises(label, exc, fn):
    try:
        fn()
    except exc as e:
        print(f"{label}: PASS ({type(e).__name__})")
        return
    except BaseException as e:  # noqa: BLE001
        print(f"{label}: FAIL (got {type(e).__name__}: {e})")
        return
    print(f"{label}: FAIL (no exception)")


sky = ue4ss.find_first_of("BP_Sky_Sphere_C")
if sky is None:
    skip("variables of BP_Sky_Sphere", "this game has no BP_Sky_Sphere")
else:
    exact = getattr(sky, "Sun height")
    check("a variable is readable under its identifier form", sky.Sun_height == exact, f"{sky.Sun_height} vs {exact}")
    sky.Sun_height = 21.5
    check("and writable under it", abs(getattr(sky, "Sun height") - 21.5) < 1e-4, str(getattr(sky, "Sun height")))
    sky.Sun_height = exact
    check("an object variable too", sky.Sky_material == getattr(sky, "Sky material"))
    raises("a made-up name with an underscore still fails", AttributeError, lambda: sky.Sun_hight)
    raises("a different case is another name", AttributeError, lambda: sky.sun_Height_x)

overview = ue4ss.find_first_of("OverviewScreen_C")
if overview is None:
    skip("function with spaces in its name", "this game has no OverviewScreen_C")
else:
    exact_function = getattr(overview, "Get Widget For Current State")
    check("a function is found under its identifier form", overview.Get_Widget_For_Current_State() == exact_function(), "same widget")
    raises("a made-up function name with underscores still fails", AttributeError, lambda: overview.Get_Widget_For_Next_State)
print("identifier alias finished")
