# Benchmarks

How fast is fyro compared with plain FastAPI, Django and Flask? This page shows what was measured, what the numbers mean in plain words, and where fyro could get faster.

!!! note "Read these numbers with care"
    They come from one laptop, on one day, with a small test app. Use them to compare the frameworks with each other, not as promises about your server.

## The test

All four frameworks serve the same blog API: one `Post` table (title, body, published) holding 10,000 rows in the same PostgreSQL 16 database.

| | Server | Database driver |
|---|---|---|
| **fyro** 0.1.0 | uvicorn, 4 workers | SQLAlchemy 2.1 (async) + asyncpg |
| **FastAPI** 0.159, written by hand | uvicorn, 4 workers | SQLAlchemy 2.1 (async) + asyncpg |
| **Django** 6.1 + Django REST Framework 3.18 | gunicorn, 4 workers × 8 threads | psycopg 3, with Django's connection pool |
| **Flask** 3.1 + Flask-SQLAlchemy 3.1 | gunicorn, 4 workers × 8 threads | SQLAlchemy 2.1 + psycopg 3 |

Each framework answers three requests:

- **`get`**: "give me post #1234". One row by its id.
- **`list10`**: "give me 10 posts, starting at post 5,000, and tell me how many posts exist". One page of rows plus a count of the whole table.
- **`create`**: "save this new post".

The load tool [oha](https://github.com/hatoo/oha) sends requests over 64 connections, nonstop, for 15 seconds per request type. Every framework was tested 3 times, in a different order each round. Before each test the table was reset to exactly 10,000 rows. The numbers below are the middle (median) result of the 3 rounds.

Machine: Apple M4 (10 cores), macOS, Python 3.14, Postgres in Docker. The load tool, the servers and the database all ran on the same laptop.

## Results

### Requests per second (higher is better)

| | `get` | `list10` | `create` |
|---|---|---|---|
| fyro | 2,336 | 1,176 | 1,900 |
| FastAPI | **3,058** | **1,626** | **2,289** |
| Django + DRF | 1,703 | 1,020 | 1,769 |
| Flask | 1,827 | 1,035 | 1,081 |

### Time to answer, in milliseconds (lower is better)

"p50" is the typical request: half were faster than this. "p99" is a slow request: 1 in 100 was slower than this.

| | `get` p50 / p99 | `list10` p50 / p99 | `create` p50 / p99 |
|---|---|---|---|
| fyro | 25.1 / 82.6 | 49.0 / 155.8 | 30.2 / 95.8 |
| FastAPI | 16.7 / 77.3 | 34.1 / 115.8 | 25.3 / 82.4 |
| Django + DRF | 34.2 / 95.6 | 60.0 / 145.1 | 34.1 / 77.0 |
| Flask | 29.0 / 88.5 | 50.2 / 210.2 | 55.5 / 131.9 |

### How much the rounds varied

The same test, run three times, gave quite different numbers on this laptop:

| `get`, requests per second | Round 1 | Round 2 | Round 3 |
|---|---|---|---|
| fyro | 3,711 | 2,336 | 2,254 |
| FastAPI | 3,058 | 2,320 | 3,513 |

So a difference of less than about 25% between two frameworks is within the noise.

### Everything else

| | Lines you write | Packages installed | Install size | Startup | What you get |
|---|---|---|---|---|---|
| fyro | 12 | 35 | 37 MB | 0.70 s | API, docs, migrations, admin, login |
| FastAPI | 55 | 17 | 25 MB | 0.75 s | API, docs |
| Django + DRF | 53 | 9 | 54 MB | 0.32 s | API, migrations |
| Flask | 39 | 13 | 32 MB | 0.35 s | API (input checks written by hand) |

"Lines you write" counts the code for this test app, without blank lines and comments. "Startup" is the time from starting the server until it answers its first request. Memory was 270 to 430 MB for every framework (4 workers each), but macOS memory numbers are not precise enough to compare.

## What the results mean, in plain words

Think of each framework as a restaurant kitchen with 4 cooks, all cooking from the same pantry (the database).

- **fyro and FastAPI are "async" kitchens.** A cook sends an order to the pantry and takes the next order while waiting. Django and Flask cooks wait by the pantry until the food comes back. That is why the async kitchens served the most single-row reads (`get`).
- **Everyone was close on `list10`.** Counting all 10,000 posts happens inside the database, and it is the same slow job no matter who asks.
- **Flask was slowest at `create`.** After saving a post, Flask-SQLAlchemy reads it back from the database by default. That is one extra trip for every save.
- **fyro was a little slower than plain FastAPI,** but the gap is within the noise. fyro does more checking on every request, the way a cook might double-check each order.
- **fyro needs the least code by far:** 12 lines instead of 39 to 55, and it includes an admin, migrations and login, which the others don't have in this test.

## Where the time goes inside fyro

A load test shows which request is slow, not why. So fyro and the hand-written FastAPI app were also profiled: each one handled 3,000 requests in a single process while Python recorded where the time went.

| Request | fyro | FastAPI by hand |
|---|---|---|
| `get` | 1,343 requests/s (0.74 ms each) | 1,217 requests/s (0.82 ms each) |
| `list10` | 164 requests/s (6.1 ms each) | 177 requests/s (5.6 ms each) |

With the noise of a full load test removed, fyro and plain FastAPI run at the same speed. The breakdown for a fyro `get`:

| Where the time goes | Share |
|---|---|
| Waiting for the database | 50% |
| SQLAlchemy (building queries, turning rows into objects) | 16% |
| Python built-ins and the standard library | 18% |
| FastAPI and Starlette (routing, checking input) | 12% |
| asyncpg (the database driver) | 2% |
| **fyro's own code** | **2%** |

Inside the database:

| Query | Time |
|---|---|
| Fetch one post by id | 0.04 ms |
| `count(*)` of 10,000 posts | 2.2 ms |
| Skip 5,000 posts and take 10 (`OFFSET 5000`) | 1.1 ms |

Every request also makes several trips to the database. A `get` sends `BEGIN`, `SELECT` and `COMMIT`: three round trips for a query that takes 0.04 ms. A `list10` sends `BEGIN`, two `SELECT`s and `COMMIT`.

### Does a faster web server help?

fyro was also run on [Granian](https://github.com/emmett-framework/granian), a web server written in Rust, instead of uvicorn. Two rounds gave mixed results: Granian won round 1 and uvicorn won round 2, both within the noise. The web server is not the slow part.

## Where fyro can get faster (the 80/20)

A few changes could give most of the gain. In order of payoff:

1. **Don't count the whole table on every list request.** `count(*)` is about a third of a `list10` request, and it gets slower as the table grows. In an early test where the table had grown to about 200,000 rows, `list10` fell from 1,737 to 307 requests per second.
    - **Done:** counting no longer sorts. The count used to keep the list's `ORDER BY`, so `?order_by=title` made Postgres sort every row just to count them. On 50,000 rows, sorted by title, 4 workers: 180 requests per second before the fix, 802 after.
    - **Done:** `?with_total=false` skips the count. Same test: 1,112 requests per second.
    - **Still open:** keyset paging ("posts after id 5000") instead of `OFFSET`.
2. **Fewer database round trips.** A read-only request doesn't need its own `BEGIN` and `COMMIT`. Removing them would save two of the three round trips on every `get`.
3. **Skip ORM objects on reads.** SQLAlchemy builds a full Python object for every row, and then fyro copies it into the `Read` schema. Reading plain rows straight into `Read` would cut most of the 16% spent in SQLAlchemy.
4. **Faster JSON.** Building responses with orjson or msgspec helps most on large list pages.

Making fyro's own code faster is not worth it yet: it is only 2% of the time.

## Could Rust make fyro faster?

Several Python frameworks put Rust underneath and keep Python on top. The parts that could move to Rust in fyro:

| Part | Gain | Notes |
|---|---|---|
| Turning database rows straight into JSON | Large, on lists | Python never builds a row object |
| Routing and checking input (now FastAPI and Starlette) | Small to medium | About 12% of a request today |
| The web server | None measured | Granian made no clear difference |
| Waiting for the database | None | Rust can't make Postgres count faster |

Half of every request is spent waiting for the database. So even if every line of Python became free, fyro would get **at most about 2× faster** on these requests. Fixes 1 to 3 above don't need Rust, and they target that waiting time directly.

## How fair is this?

Things that were kept the same: the database, the table, the data, the machine, the number of workers and connections, and the reset before every test.

Things that differ, which you should know about:

- **Async against threads.** fyro and FastAPI use async workers. Django and Flask use 8 threads per worker. A different thread count would change the Django and Flask numbers.
- **Different amounts of work.** fyro checks more on every request: text Postgres can store, integer ranges, and a database session per request. Django REST Framework builds `next` and `previous` links. The Flask app's input checks are minimal.
- **Connection pools differ.** SQLAlchemy keeps 5 connections per worker (up to 15). Django's pool was set to at most 15.
- **One laptop did everything.** The load tool competed with the servers and the database for the same CPU, and Postgres ran inside Docker's virtual machine on macOS.
- **Small rows, a small table, no network.** A real server with a network between the app and the database would add delay to every round trip, which makes fix 2 matter more.
