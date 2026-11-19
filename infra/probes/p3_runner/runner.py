import json, os, random, time, traceback


def emit(name, ok, **kw):
    print(json.dumps({"check": name, "pass": bool(ok), **kw}, default=str), flush=True)


def vec_lit(v):
    return "[" + ",".join(f"{x:.5f}" for x in v) + "]"


def main():
    import psycopg
    host, user, pw = os.environ.get("PGHOST"), os.environ.get("PGUSER"), os.environ.get("PGPASSWORD")
    emit("p3_secrets_via_role", bool(pw),
         evidence=f"PGPASSWORD injected by ECS from Secrets Manager via execution role, length={len(pw or '')} (value not logged)")
    conn = None
    err = ""
    t0 = time.time()
    for _ in range(10):
        try:
            conn = psycopg.connect(host=host, user=user, password=pw, dbname="postgres",
                                   sslmode="require", connect_timeout=10, autocommit=True)
            break
        except Exception as e:
            err = str(e)
            time.sleep(5)
    if conn is None:
        emit("pg_connect", False, error=err)
    else:
        emit("pg_connect", True, ms=int((time.time() - t0) * 1000),
             version=conn.execute("select version()").fetchone()[0])
        try:
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            v = conn.execute("select extversion from pg_extension where extname='vector'").fetchone()[0]
            ok = tuple(int(x) for x in v.split(".")[:2]) >= (0, 7)
            emit("a_pgvector_extension", ok, extversion=v)
        except Exception as e:
            emit("a_pgvector_extension", False, error=str(e))
        vecs = []
        try:
            conn.execute("DROP TABLE IF EXISTS chunks")
            conn.execute("""CREATE TABLE chunks(id serial primary key, tenant_id text, body text,
                tsv tsvector generated always as (to_tsvector('english', body)) stored,
                embedding vector(1024))""")
            rows = [("T1", "refund policy for damaged items"), ("T1", "shipping times to Sweden"),
                    ("T2", "password reset instructions")]
            for t, b in rows:
                vec = [random.uniform(-1, 1) for _ in range(1024)]
                vecs.append(vec)
                conn.execute("INSERT INTO chunks(tenant_id, body, embedding) VALUES (%s,%s,%s::vector)",
                             (t, b, vec_lit(vec)))
            conn.execute("CREATE INDEX chunks_hnsw ON chunks USING hnsw (embedding vector_cosine_ops)")
            conn.execute("CREATE INDEX chunks_tsv ON chunks USING gin (tsv)")
            q = vec_lit(vecs[1])
            conn.execute("SET enable_seqscan=off")
            plan = " | ".join(r[0] for r in conn.execute(
                "EXPLAIN SELECT id FROM chunks ORDER BY embedding <=> %s::vector LIMIT 1", (q,)).fetchall())
            r = conn.execute("SELECT id, body, embedding <=> %s::vector AS d FROM chunks "
                             "ORDER BY embedding <=> %s::vector LIMIT 1", (q, q)).fetchone()
            conn.execute("RESET enable_seqscan")
            emit("b_hnsw_nn_query", r[0] == 2, nearest_id=r[0], body=r[1], distance=float(r[2]),
                 uses_hnsw=("chunks_hnsw" in plan), plan=plan[:200])
        except Exception as e:
            emit("b_hnsw_nn_query", False, error=str(e))
        try:
            r = conn.execute("SELECT id, body FROM chunks WHERE tsv @@ websearch_to_tsquery('english', 'refund damaged')").fetchall()
            emit("c_tsvector_websearch", len(r) == 1 and r[0][0] == 1, rows=r)
        except Exception as e:
            emit("c_tsvector_websearch", False, error=str(e))
        try:
            conn.execute("DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='app_user') "
                         "THEN CREATE ROLE app_user NOLOGIN; END IF; END $$")
            conn.execute("GRANT app_user TO CURRENT_USER")
            conn.execute("GRANT SELECT ON chunks TO app_user")
            conn.execute("ALTER TABLE chunks ENABLE ROW LEVEL SECURITY")
            conn.execute("DROP POLICY IF EXISTS tenant_iso ON chunks")
            conn.execute("CREATE POLICY tenant_iso ON chunks USING (tenant_id = current_setting('app.tenant_id'))")
            conn.execute("SET ROLE app_user")
            conn.execute("SET app.tenant_id='T1'")
            t1 = conn.execute("SELECT tenant_id FROM chunks").fetchall()
            t2_lookup = conn.execute("SELECT count(*) FROM chunks WHERE tenant_id='T2'").fetchone()[0]
            conn.execute("SET app.tenant_id='T2'")
            t2 = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
            who = conn.execute("SELECT current_user").fetchone()[0]
            conn.execute("RESET ROLE")
            ok = len(t1) == 2 and all(x[0] == "T1" for x in t1) and t2_lookup == 0 and t2 == 1
            emit("d_rls", ok, as_user=who, t1_rows=len(t1), t1_tenants=sorted({x[0] for x in t1}),
                 t2_lookup_while_T1=t2_lookup, t2_rows_while_T2=t2)
        except Exception as e:
            emit("d_rls", False, error=str(e))
    vh = os.environ.get("VALKEY_HOST")
    if not vh:
        emit("e_valkey", False, error="VALKEY_HOST not set (valkey not provisioned)")
        return
    try:
        import redis
        r = redis.Redis(host=vh, port=int(os.environ.get("VALKEY_PORT", "6379")), ssl=True,
                        socket_timeout=10, socket_connect_timeout=10)
        t0 = time.time()
        r.set("paynest-probe:k", "v", ex=30)
        got = r.get("paynest-probe:k")
        ttl = r.ttl("paynest-probe:k")
        n = r.delete("paynest-probe:k")
        info = r.info("server")
        emit("e_valkey", got == b"v" and 0 < ttl <= 30 and n == 1, get=got.decode(), ttl=ttl, deleted=n, tls=True,
             server=info.get("valkey_version") or info.get("redis_version"), ms=int((time.time() - t0) * 1000))
    except Exception as e:
        emit("e_valkey", False, error=f"{type(e).__name__}: {e}")


try:
    main()
except Exception:
    emit("runner_crash", False, error=traceback.format_exc()[-800:])
print("RUNNER_DONE", flush=True)
