from graph import MunichAirspaceDigitalTwin


def print_section(title):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def main():
    print_section("BUILDING MUNICH AIRSPACE DIGITAL TWIN")

    twin = MunichAirspaceDigitalTwin()
    twin.build_world()

    stats = twin.get_network_stats()

    print("Digital twin created successfully.")
    print(f"Total nodes: {stats['total_nodes']}")
    print(f"Pads: {stats['pads']}")
    print(f"Hospitals: {stats['hospitals']}")
    print(f"Charging hubs: {stats['charging_hubs']}")
    print(f"Air corridors: {stats['total_routes']}")
    print(f"Available nodes: {stats['available_nodes']}")
    print(f"Busy nodes: {stats['busy_nodes']}")
    print(f"Full nodes: {stats['full_nodes']}")
    print(f"Total aircraft on routes: {stats['total_aircraft_on_routes']}")

    print_section("PAD / NODE AVAILABILITY")

    for node_name, availability in twin.get_all_availability().items():
        print(
            f"{node_name} | "
            f"type={availability['type']} | "
            f"capacity={availability['capacity']} | "
            f"current_load={availability['current_load']} | "
            f"available_slots={availability['available_slots']} | "
            f"status={availability['status']}"
        )

    print_section("SAMPLE AVAILABILITY UPDATE")

    target_pad = "TUM Main Campus"

    before = twin.get_pad_availability(target_pad)
    print(f"Before landing at {target_pad}: {before}")

    success = twin.occupy_landing_slot(target_pad)
    print(f"Landing slot occupied: {success}")

    after = twin.get_pad_availability(target_pad)
    print(f"After landing at {target_pad}: {after}")

    print_section("AIR CORRIDORS WITH CONGESTION AND COSTS")

    for route in twin.routes:
        print(
            f"{route.start} <--> {route.end} | "
            f"type={route.route_type} | "
            f"distance={route.distance} km | "
            f"battery={route.battery_cost} | "
            f"noise={route.noise_penalty} | "
            f"weather={route.weather_penalty} | "
            f"aircraft_count={route.current_aircraft_count} | "
            f"traffic={route.traffic_penalty} | "
            f"total_cost={route.total_cost}"
        )

    print_section("SAMPLE ROUTE CONGESTION UPDATE")

    route_start = "Munich Airport"
    route_end = "Munich Central Station (Hauptbahnhof)"

    before_route = twin.get_route(route_start, route_end).to_dict()
    print("Before congestion update:")
    print(before_route)

    updated_route = twin.update_route_congestion(
        route_start,
        route_end,
        current_aircraft_count=8,
    )

    print("\nAfter congestion update:")
    print(updated_route)

    print_section("SAMPLE LOWEST-COST PATHS")

    sample_queries = [
        ("Munich Airport", "TUM Klinikum Rechts der Isar"),
        ("Allianz Arena", "München Klinik Neuperlach"),
        ("Munich Central Station (Hauptbahnhof)", "Großhadern Clinic"),
        ("Marienplatz", "Charging Hub D - Großhadern Zone"),
    ]

    highlighted_path = None

    for index, (start, end) in enumerate(sample_queries, start=1):
        path, distance_km, total_cost = twin.find_shortest_path(start, end)

        print(f"\nQuery {index}")
        print(f"From: {start}")
        print(f"To:   {end}")
        print(f"Path: {' -> '.join(path)}")
        print(f"Total air distance: {distance_km} km")
        print(f"Total route cost: {total_cost}")

        if index == 1:
            highlighted_path = path

    print_section("EXPORTING WORLD JSON")

    twin.export_world_json()
    print("world.json created at: backend/world.json")

    print_section("CREATING INTERACTIVE MAP")

    map_file = twin.create_interactive_map(
        filename="backend/munich_airspace_map.html",
        highlight_path=highlighted_path,
    )

    print(f"Map created at: {map_file}")
    print("Open backend/munich_airspace_map.html in your browser.")

    print_section("DONE")


if __name__ == "__main__":
    main()