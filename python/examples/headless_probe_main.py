import ue4ss
print("headless probe: world =", ue4ss.find_first_of("World"), "| actors:", len(ue4ss.find_all_of("Actor")))
print("headless probe finished marker")
