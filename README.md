# Carpool App — Routing Prototype

This is the v0 routing prototype. It does one thing: given a list of families with
addresses, a driver, and a destination, it computes the optimal pickup order and
prints the route with departure time.

No web app yet. No database yet. No SMS yet. Just the core routing math, working
against real addresses via Google Maps, so we can validate that the routing piece
is solid before building anything else on top of it.

## Setup

1. Get a Google Maps API key (see main project notes). Make sure these APIs are
   enabled: Routes API, Geocoding API.

2. Create a `.env` file in this directory:
   ```
   GOOGLE_MAPS_API_KEY=your_key_here
   ```

3. Install dependencies:
   ```
   pip install -r requirements.txt
   ```

4. Edit `families.py` with your actual carpool families and addresses.

5. Run it:
   ```
   python route.py
   ```

## What it does

- Geocodes every address once and caches the result (so we don't pay for
  geocoding on every run).
- Asks Google Routes API to compute the optimal pickup order given a driver's
  home, the kids' pickup addresses, and the destination.
- Prints the route with estimated departure time so the driver arrives on time.

## What it doesn't do yet

- Pick the driver (you specify it as an argument)
- Handle nightly confirmations
- Send notifications
- Track equity / mileage
- Have a UI

Those come next, once we trust the routing.

## Data model notes

Even though this is a CLI tool, the data classes (`Group`, `Family`, `Guardian`,
`Kid`, `Address`) are structured the way the eventual database tables will be,
including `group_id` everywhere. When we add a database later, the migration
will be mostly mechanical.
