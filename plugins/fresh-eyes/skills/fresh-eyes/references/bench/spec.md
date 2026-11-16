# Spec — Wayfarer (seeded bench)
Updated: 2026-11-16

## Global
- G-copy: English only, short labels. (source: bench)

## booking — Booking summary
Purpose: every booked item with its price, then payment.
- must booking#1: the pay bar stays pinned at the bottom with the total. (source: bench)
### Invariants
- inv booking#2: the total equals the sum of the row prices. (source: bench)

## flight — Flight details
Purpose: one flight's times and price before booking.
- must flight#1: departure and arrival in local time with the airport. (source: bench)
### Invariants
- inv flight#2: arrival = departure + duration, in the arrival airport's time zone; Berlin and Rome are both on
  Central European Time. (source: bench)
- inv flight#3: the total equals adults × the fare. (source: bench)

## profile-edit — Edit profile
Purpose: change name, home city and about.
- must profile-edit#1: Cancel, Save changes and Preview stay in one row. (source: bench)

## traveller — Traveller profile
Purpose: another traveller's trips; follow them.
- must traveller#1: Follow turns into Following at once and the count goes up by one. (source: bench)

## comments — Comments
Purpose: read and add comments under a trip.
- must comments#1: tapping the field opens the keyboard with the field above it. (source: bench)

## post — Post
Purpose: one trip post.
- must post#1: the place chip opens the phone's map app at that place. (source: bench)
- must post#2: «Plan this trip» opens the trip planner with this post's place. (source: bench)
