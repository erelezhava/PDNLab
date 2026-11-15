import sys, json, pathlib
# usage: python experiments/exp08_analyze_3v3.py BOARD.kicad_pcb OUTDIR   (needs shapely, gmsh, meshio, matplotlib)
# Mesh: h = 0.5 mm with local refinement at via holes and through-hole pads (96-sided holes, growth 0.15 mm/mm).
# The first run used no local refinement; its numbers are kept in OUTDIR/checks_old_mesh_h0.5.json.
SIDES, GROWTH = 96, 0.15
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src")); sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import numpy as np
import exp08_real_net as e
import meshio
B, OUT = sys.argv[1], sys.argv[2]
pathlib.Path(OUT).mkdir(parents=True, exist_ok=True)
board = e.read_net(B, "+3V3"); plating = 0.025
layers = e.build_layers(board, plating, rim_segments=SIDES)
meshes = {n: e.mesh_layer(n, layers[n], board["pads"], OUT, 0.5, "gmsh", growth=GROWTH) for n in board["copper"] if layers[n]["polys"]}
model = e.assemble(board, layers, meshes, 1.724e-8, plating)
loads = [p["ref"]+"."+p["pin"] for p in board["pads"] if p["type"] == "power_in"]
src = {"J4": ["J4.6","J4.10","J4.13","J4.16"], "J1": ["J1.1"], "J5": ["J5.1"]}
res = {}
for s, pins in src.items():
    ids, Z, unreach, ctx = e.transfer_matrix(model, pins, loads)
    sym = float(np.max(np.abs(Z - Z.T)) / np.max(Z))
    diag = np.diag(Z)
    bound = bool(np.all(Z <= np.minimum.outer(diag, diag) + 1e-12))   # Z[p,k] <= min(Z[p,p], Z[k,k])
    eig = float(np.linalg.eigvalsh((Z + Z.T) / 2).min())
    # scenario: 100 mA at each IC supply pin, solved by the repo's own solver
    cur = {k: 0.1 for k in ids}
    v, chk = e.solve_scenario(model, ctx, cur)
    drop_matrix = Z @ np.full(len(ids), 0.1)
    drop_solver = np.array([v[model["pin_red"][k][0]] for k in ids])
    res[s] = dict(loads=ids, selfZ_mohm=(diag*1e3).tolist(), symmetry_err=sym, bound_ok=bound, min_eig=eig,
                  scenario_100mA_drop_mV=(drop_solver*1e3).tolist(), matrix_vs_solver_max_diff_uV=float(np.max(np.abs(drop_matrix-drop_solver))*1e6),
                  conservation=chk, unreachable=unreach)
    print(s, "sym err %.2e" % sym, "Z<=min(diag):", bound, "min eig %.2e" % eig, "| 11 pins x 100mA = %.2f A" % chk["load_current_A"],
          "source current %.9f A, imbalance %.2e A" % (chk["source_current_A"], chk["imbalance_A"]),
          "| max drop %.2f mV, matrix vs solver %.2e uV" % (max(drop_solver)*1e3, res[s]["matrix_vs_solver_max_diff_uV"]))
    if s == "J4": vJ4 = v
json.dump(res, open(OUT + "/checks.json", "w"), indent=1)
# voltage map (J4 source, 100 mA per IC supply pin = 1.2 A envelope)
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt, matplotlib.tri as mtri
names = list(meshes); fig, axs = plt.subplots(1, len(names), figsize=(6*len(names), 6.2))
vmax = max(vJ4) * 1e3
for ax, n in zip(np.atleast_1d(axs), names):
    pts, tri, _ = meshes[n]; off = model["offsets"][n]
    vv = vJ4[model["node_map"][off:off+len(pts)]] * 1e3
    t = mtri.Triangulation(pts[:,0], -pts[:,1], tri)
    cs = ax.tripcolor(t, vv, shading="gouraud", cmap="viridis", vmin=0, vmax=vmax)
    for p in board["pads"]:
        if n in p["layers"]:
            lab = p["ref"]+"."+p["pin"]
            if lab in loads: ax.plot(p["centre"][0], -p["centre"][1], "r.", ms=5)
            if lab in src["J4"]: ax.plot(p["centre"][0], -p["centre"][1], "k^", ms=7)
    ax.set_title(f"{n} (+3V3 copper only)"); ax.set_aspect("equal"); ax.set_xlabel("x [mm]"); ax.set_ylabel("-y [mm]")
fig.colorbar(cs, ax=axs, label="voltage drop from J4 [mV]", shrink=0.8)
fig.suptitle("+3V3 drop, supply at J4 (black triangles), 100 mA at each of 11 IC supply pins (red) = 1.1 A envelope", fontsize=11)
fig.savefig(OUT + "/v3v3_drop_map.png", dpi=110, bbox_inches="tight")
# ParaView files
for n in names:
    pts, tri, _ = meshes[n]; off = model["offsets"][n]
    meshio.write(f"{OUT}/v3v3_{n.replace('.','_')}.vtu", meshio.Mesh(np.c_[pts*1e-3, np.zeros(len(pts))], [("triangle", tri)],
                 point_data={"drop_V": vJ4[model["node_map"][off:off+len(pts)]]}))
print("max drop over all nodes (mV):", round(vmax, 2))
