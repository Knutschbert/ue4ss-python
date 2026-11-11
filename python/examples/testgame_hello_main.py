# Smallest check that the mod runs in the test game: print what the game has.
import ue4ss

actors = ue4ss.find_all_of("PyTestActor")
print("test actors:", [a.get_fname().to_string() for a in actors])
print("world:", ue4ss.find_first_of("World"))
print("hello from the test game done")
