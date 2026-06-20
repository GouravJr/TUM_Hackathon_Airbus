from graph import MunichAirspaceDigitalTwin
import networkx as nx


def print_section(title):
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)


def print_route_result(result):
    print(f"Mission type: {result['mission_type']}")
    print(f"From: {result['start']}")
    print(f"To: {result['destination']}")
    print(f"Path: {' -> '.join(result['path'])}")
    print(f"Distance: {result['distance_km']} km")
    print(f"Total mission cost: {result['total_cost']}")


def safe_find_route(twin, start, destination, mission_type):
    try:
        return twin.find_best_route(
            start=start,
            destination=destination,
            mission_type=mission_type,
        )
    except nx.NetworkXNoPath:
        print(
            f"No valid {mission_type} route found from "
            f"{start} to {destination}."
        )
        return None


def main():
    print_section("BUILDING PHASE 1.6 MUNICH AIRSPACE DIGITAL TWIN")

    twin = MunichAirspaceDigitalTwin()
    twin.build_world()

    stats = twin.get_network_stats()

    print("Digital twin created successfully.")
    print(f"Total nodes: {stats['total_nodes']}")
    print(f"Pads: {stats['pads']}")
    print(f"Hospitals: {stats['hospitals']}")
    print(f"Charging hubs: {stats['charging_hubs']}")
    print(f"Fixed air corridors: {stats['total_routes']}")
    print(f"Aircraft currently on routes: {stats['total_aircraft_on_routes']}")
    print(f"Closed corridors: {stats['closed_corridors']}")
    print(f"Restricted corridors: {stats['restricted_corridors']}")

    print_section("NODE AVAILABILITY")

    for node_name, availability in twin.get_all_availability().items():
        print(
            f"{node_name} | "
            f"type={availability['type']} | "
            f"capacity={availability['capacity']} | "
            f"load={availability['current_load']} | "
            f"available={availability['available_slots']} | "
            f"status={availability['status']}"
        )

    print_section("FIXED AIR CORRIDORS")

    for route in twin.routes:
        print(
            f"{route.start} <--> {route.end} | "
            f"type={route.route_type} | "
            f"layer={route.altitude_layer} | "
            f"status={route.safety_status} | "
            f"distance={route.distance} km | "
            f"aircraft={route.current_aircraft_count} | "
            f"traffic={route.traffic_penalty} | "
            f"base_cost={route.base_total_cost}"
        )

    print_section("MISSION-SPECIFIC ROUTING: PASSENGER ROUTE")

    passenger_start = "Munich Airport"
    passenger_destination = "Marienplatz"

    passenger_route = safe_find_route(
        twin=twin,
        start=passenger_start,
        destination=passenger_destination,
        mission_type="passenger",
    )

    if passenger_route:
        print("\nPassenger route:")
        print_route_result(passenger_route)

    print_section("MISSION-SPECIFIC ROUTING: MEDICAL AND ORGAN ROUTES")

    emergency_start = "Munich Airport"
    emergency_destination = "TUM Klinikum Rechts der Isar"

    medical_route = safe_find_route(
        twin=twin,
        start=emergency_start,
        destination=emergency_destination,
        mission_type="medical",
    )

    organ_route = safe_find_route(
        twin=twin,
        start=emergency_start,
        destination=emergency_destination,
        mission_type="organ",
    )

    if medical_route:
        print("\nMedical route:")
        print_route_result(medical_route)

    if organ_route:
        print("\nOrgan transport route:")
        print_route_result(organ_route)

    print_section("DYNAMIC WEATHER UPDATE EXAMPLE")

    print("Adding storm penalty on TUM Garching Campus -> Marienplatz")

    twin.update_weather_penalty(
        "TUM Garching Campus",
        "Marienplatz",
        weather_penalty=40,
    )

    passenger_after_weather = safe_find_route(
        twin=twin,
        start=passenger_start,
        destination=passenger_destination,
        mission_type="passenger",
    )

    if passenger_after_weather:
        print("\nPassenger route after weather update:")
        print_route_result(passenger_after_weather)

    print_section("CORRIDOR CLOSURE EXAMPLE")

    print("Closing Marienplatz -> TUM Klinikum Rechts der Isar corridor")

    twin.close_corridor(
        "Marienplatz",
        "TUM Klinikum Rechts der Isar",
    )

    medical_after_closure = safe_find_route(
        twin=twin,
        start=emergency_start,
        destination=emergency_destination,
        mission_type="medical",
    )

    if medical_after_closure:
        print("\nMedical route after corridor closure:")
        print_route_result(medical_after_closure)

    print_section("RESTRICTED CORRIDOR EXAMPLE")

    print("Restricting Munich Airport -> Munich Central Station corridor")

    twin.restrict_corridor(
        "Munich Airport",
        "Munich Central Station (Hauptbahnhof)",
    )

    passenger_after_restriction = safe_find_route(
        twin=twin,
        start=passenger_start,
        destination=passenger_destination,
        mission_type="passenger",
    )

    medical_after_restriction = safe_find_route(
        twin=twin,
        start=emergency_start,
        destination=emergency_destination,
        mission_type="medical",
    )

    if passenger_after_restriction:
        print("\nPassenger route after restricted corridor:")
        print_route_result(passenger_after_restriction)

    if medical_after_restriction:
        print("\nMedical route after restricted corridor:")
        print_route_result(medical_after_restriction)

    print_section("NEAREST CHARGING HUB EXAMPLE")

    nearest_charger = twin.find_nearest_charging_hub("Schwabing")
    print_route_result(nearest_charger)

    print_section("NEAREST HOSPITAL EXAMPLE")

    nearest_hospital = twin.find_nearest_hospital("Messe München")
    print_route_result(nearest_hospital)

    print_section("ROUTE CONGESTION UPDATE EXAMPLE")

    twin.update_route_congestion(
        "Munich Airport",
        "Munich Central Station (Hauptbahnhof)",
        current_aircraft_count=10,
    )

    updated_route = twin.get_route(
        "Munich Airport",
        "Munich Central Station (Hauptbahnhof)",
    )

    print(updated_route.to_dict())

    print_section("PAD AVAILABILITY UPDATE EXAMPLE")

    target_pad = "TUM Main Campus"

    before = twin.get_pad_availability(target_pad)
    print(f"Before landing at {target_pad}: {before}")

    success = twin.occupy_landing_slot(target_pad)
    print(f"Landing slot occupied: {success}")

    after = twin.get_pad_availability(target_pad)
    print(f"After landing at {target_pad}: {after}")

    twin.release_landing_slot(target_pad)

    released = twin.get_pad_availability(target_pad)
    print(f"After releasing landing slot at {target_pad}: {released}")

    print_section("EVENT LOG")

    for event in twin.event_log[-15:]:
        print(f"[{event['time']}] {event['event_type']}: {event['message']}")

    print_section("EXPORTING WORLD JSON")

    twin.export_world_json()
    print("world.json created at: backend/world.json")

    print_section("CREATING INTERACTIVE MAP")

    highlight_path = None

    if medical_after_closure:
        highlight_path = medical_after_closure["path"]
    elif medical_route:
        highlight_path = medical_route["path"]
    elif passenger_after_weather:
        highlight_path = passenger_after_weather["path"]
    elif passenger_route:
        highlight_path = passenger_route["path"]

    map_file = twin.create_interactive_map(
        filename="backend/munich_airspace_map.html",
        highlight_path=highlight_path,
    )

    print(f"Map created at: {map_file}")
    print("Open backend/munich_airspace_map.html in your browser.")

    print_section("DONE")


if __name__ == "__main__":
    main()