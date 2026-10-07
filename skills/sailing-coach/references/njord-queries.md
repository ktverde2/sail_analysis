# Njord queries

Adapted from the Njord MCP `get_help` examples (slugs: `list-my-boats`, `list-events-for-boat`, `list-races-with-course`, `get-fleet-race-aggregate`). If a query fails validation, don't guess: call `get_graphql_schema` for the type named in the error, as the Njord tool description instructs.

## 1. Find the boat

```graphql
query FindBoat($name: String!) {
  listBoats(limit: 10, filter: { nameMatches: $name }, sort: Name) {
    cursor
    boat { key name boatClass { name } }
  }
}
```

Variables: `{ "name": "Mojo" }`

For "what was sailing last weekend?", use `filter: { dataBetween: { start, end } }` instead.

## 2. Events for the weekend

```graphql
query EventsForBoat($boatKey: ID!, $from: Date!, $to: Date!) {
  boat(key: $boatKey) {
    eventsBetween(startTime: $from, endTime: $to) {
      key name startTime endTime timezone
    }
  }
}
```

Use a window a day wider than the weekend on each side, because event bounds are sometimes loose.

## 3. Races with course

```graphql
query ListRacesForEvent($eventKey: ID!) {
  event(key: $eventKey) {
    key name timezone
    races {
      key name startTime endTime
      course { elements { type name coord1 { lat lon } coord2 { lat lon } } }
    }
  }
}
```

Races with `course: null` can't produce leg stats or `fleetRaceInfo`. For those, fall back to `get_data` over `startTime`→`endTime` plus raceInfo events. On multi-lap courses that reuse the start line as a gate, `endTime` may stop at the first lap, so check against leg count.

## 4. Fleet race aggregate (the debrief backbone)

```graphql
query FleetRaceView($raceKey: ID!, $boatKeys: [ID!]) {
  race(key: $raceKey) {
    key name
    fleetRaceInfo(boatKeys: $boatKeys) {
      boats { key name }
      startData {
        boat { key }
        lineCross lineCrossRank
        belowLineGun belowLineGunRank
        sogGun sogGunRank sogLine
        lineLength crossLineDistToStbdRelative distanceCrossToBiasPointRelative
        sogGunMinus5Relative sogGunPlus5Relative
        lastManeuverSec lastManeuverTimeToKill laneXteMax
      }
      legsData {
        legNumber type
        boats { ...leg }
      }
      raceData { boats { ...leg } }
    }
  }
}

fragment leg on FleetRaceLegBoatData {
  boat { key }
  duration durationRank
  avgTws avgTwd
  avgSogKts avgSogKtsRank
  avgVmcKts avgVmcKtsRank
  legStartTime legEndTime legEndTimeRank
  timeToLeader
  tacks tacksRank gybes
  liftInfos { referenceType liftAvg liftedRatio }
}
```

Omit `boatKeys` to get the whole fleet, which is needed for meaningful ranks. Key fields:

- `belowLineGun` (m): negative = over the line. `lineCross` (s after gun): negative = early.
- `crossLineDistToStbdRelative`: 0 = RC end, 1 = pin.
- `sogGunMinus5Relative` / `sogGunPlus5Relative`: SOG 5 s before and after the gun as a ratio of `sogGun`.
- Rank delta from start to first mark: `legsData[0].boats[i].legEndTimeRank` vs. `startData[i].lineCrossRank`.
- `liftedRatio` (0–1): fraction of the leg on a lift. High ratio with a rank loss means the loss came from speed or boat handling, not shifts.

## 5. Time series (`get_data`)

Probe first:

```
get_data(boat_key, start, end, metrics=[])
```

Upwind leg for target comparison:

```
get_data(boat_key, start=legStartTime, end=legEndTime,
         metrics=["TWS","TWA_Abs","Heel_Abs","BoatSpeed","SOG"],
         exclude_maneuvers=true, resample_ms=1000, resample_average=true,
         include_race_info=false)
```

Pre-start:

```
get_data(boat_key, start=gun-120s, end=gun+30s,
         metrics=["SOG","BelowLineCalc","TimeToGunCalc","Heading"],
         race_keys=[raceKey], resample_ms=1000)
```

The CSV header is always `ISODateTimeUTC,SecondsSince1970,<metrics…>`, and missing values are empty strings. Metric names are case-sensitive. In Claude.ai, pass `delivery_mode: "embed"` if the payload is large; the sandbox writes it to `/mnt/user-data/tool_results/`. Signed-URL (`link`) mode doesn't work there.
