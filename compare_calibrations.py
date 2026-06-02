#!/usr/bin/env python3
"""Compare calibration models and datasets."""
import csv
import numpy as np
import matplotlib.pyplot as plt
from scipy.optimize import curve_fit
from pathlib import Path

def load_csv(path):
    """Load z, dpt pairs from CSV."""
    z, dpt = [], []
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            z.append(float(row['z']))
            dpt.append(float(row['dpt']))
    return np.array(z), np.array(dpt)

def r2(y_true, y_pred):
    """Compute R² score."""
    ss_res = np.sum((y_true - y_pred) ** 2)
    ss_tot = np.sum((y_true - np.mean(y_true)) ** 2)
    return 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

def rmse(y_true, y_pred):
    """Compute RMSE."""
    return np.sqrt(np.mean((y_true - y_pred) ** 2))

# Load data
new_z, new_dpt = load_csv('/home/nfc/src/OptoFly/.worktrees/panda3d/src/tools/20260602_124303_liquidlens_calibration.csv')
old_z, old_dpt = load_csv('/home/nfc/src/OptoFly/.worktrees/panda3d/calibrations/liquid_lens.csv')

print("=" * 80)
print("NEW CALIBRATION (10 points)")
print("=" * 80)
print(f"Z range: {new_z.min():.4f} – {new_z.max():.4f} m")
print(f"Dpt range: {new_dpt.min():.4f} – {new_dpt.max():.4f}")
print()

# Model 1: Linear
m, q = np.polyfit(new_dpt, new_z, 1)
new_z_pred_lin = m * new_dpt + q
new_r2_lin = r2(new_z, new_z_pred_lin)
new_rmse_lin = rmse(new_z, new_z_pred_lin)
print(f"1. LINEAR: z = {m:.6f}·dpt + {q:.6f}")
print(f"   R² = {new_r2_lin:.6f}  RMSE = {new_rmse_lin:.6f} m")
print()

# Model 2: Inverse (hyperbolic)
def inverse(x, a, b, c):
    return a / (x - b) + c

try:
    # Try b > max(dpt)
    popt, _ = curve_fit(inverse, new_dpt, new_z, p0=[-500, new_dpt.max() + 1, new_z.min()],
                        bounds=([-np.inf, new_dpt.max() + 0.1, -np.inf],
                               [np.inf, new_dpt.max() + 50, np.inf]),
                        maxfev=10000)
    a, b, c = popt
    new_z_pred_inv = inverse(new_dpt, a, b, c)
    new_r2_inv = r2(new_z, new_z_pred_inv)
    new_rmse_inv = rmse(new_z, new_z_pred_inv)
    print(f"2. INVERSE: z = {a:.6f}/(dpt - {b:.6f}) + {c:.6f}")
    print(f"   R² = {new_r2_inv:.6f}  RMSE = {new_rmse_inv:.6f} m")
    print()
    inv_ok = True
except Exception as e:
    print(f"2. INVERSE: Failed — {e}")
    print()
    inv_ok = False

# Model 3: Quadratic
p_quad = np.polyfit(new_dpt, new_z, 2)
new_z_pred_quad = np.polyval(p_quad, new_dpt)
new_r2_quad = r2(new_z, new_z_pred_quad)
new_rmse_quad = rmse(new_z, new_z_pred_quad)
print(f"3. QUADRATIC: z = {p_quad[0]:.6f}·dpt² + {p_quad[1]:.6f}·dpt + {p_quad[2]:.6f}")
print(f"   R² = {new_r2_quad:.6f}  RMSE = {new_rmse_quad:.6f} m")
print()

# Model 4: Cubic
p_cubic = np.polyfit(new_dpt, new_z, 3)
new_z_pred_cubic = np.polyval(p_cubic, new_dpt)
new_r2_cubic = r2(new_z, new_z_pred_cubic)
new_rmse_cubic = rmse(new_z, new_z_pred_cubic)
print(f"4. CUBIC: z = {p_cubic[0]:.6f}·dpt³ + {p_cubic[1]:.6f}·dpt² + {p_cubic[2]:.6f}·dpt + {p_cubic[3]:.6f}")
print(f"   R² = {new_r2_cubic:.6f}  RMSE = {new_rmse_cubic:.6f} m")
print()

# Find best model
scores = [
    ("Linear", new_r2_lin, new_rmse_lin),
    ("Inverse", new_r2_inv, new_rmse_inv) if inv_ok else None,
    ("Quadratic", new_r2_quad, new_rmse_quad),
    ("Cubic", new_r2_cubic, new_rmse_cubic),
]
scores = [s for s in scores if s is not None]
best_new = max(scores, key=lambda x: x[1])
print("=" * 80)
print(f"BEST MODEL FOR NEW CALIBRATION: {best_new[0]}")
print(f"  R² = {best_new[1]:.6f}  RMSE = {best_new[2]:.6f} m")
print("=" * 80)
print()

# Now compare with old calibration
print("=" * 80)
print("OLD CALIBRATION (15 points)")
print("=" * 80)
print(f"Z range: {old_z.min():.4f} – {old_z.max():.4f} m")
print(f"Dpt range: {old_dpt.min():.4f} – {old_dpt.max():.4f}")
print()

# Fit best model from new data to old data (for comparison)
if best_new[0] == "Linear":
    old_z_pred = m * old_dpt + q
elif best_new[0] == "Inverse":
    old_z_pred = inverse(old_dpt, a, b, c)
elif best_new[0] == "Quadratic":
    old_z_pred = np.polyval(p_quad, old_dpt)
else:  # Cubic
    old_z_pred = np.polyval(p_cubic, old_dpt)

old_r2 = r2(old_z, old_z_pred)
old_rmse = rmse(old_z, old_z_pred)

print(f"OLD data fit with {best_new[0]} model from NEW calibration:")
print(f"  R² = {old_r2:.6f}  RMSE = {old_rmse:.6f} m")
print()

# Also fit old data independently
m_old, q_old = np.polyfit(old_dpt, old_z, 1)
old_z_pred_lin = m_old * old_dpt + q_old
old_r2_lin = r2(old_z, old_z_pred_lin)
old_rmse_lin = rmse(old_z, old_z_pred_lin)
print(f"OLD data fit with LINEAR model (independent):")
print(f"  z = {m_old:.6f}·dpt + {q_old:.6f}")
print(f"  R² = {old_r2_lin:.6f}  RMSE = {old_rmse_lin:.6f} m")
print()

# Comparison summary
print("=" * 80)
print("COMPARISON SUMMARY")
print("=" * 80)
print(f"\nNEW calibration uses {len(new_z)} points, OLD uses {len(old_z)}")
print(f"Z overlap: [{max(new_z.min(), old_z.min()):.4f}, {min(new_z.max(), old_z.max()):.4f}] m")
print()
print(f"NEW calibration fit quality (best model): R² = {best_new[1]:.4f}")
print(f"OLD calibration fit quality (linear): R² = {old_r2_lin:.4f}")
print()
if best_new[1] > old_r2_lin:
    print(f"✓ NEW calibration is better (ΔR² = +{best_new[1] - old_r2_lin:.4f})")
else:
    print(f"✗ OLD calibration is better (ΔR² = {best_new[1] - old_r2_lin:.4f})")
print()

# Plot
fig, axes = plt.subplots(1, 2, figsize=(14, 5))

# New calibration
ax = axes[0]
ax.scatter(new_dpt, new_z, s=50, color="tab:blue", zorder=3, label="New data")
dpt_dense = np.linspace(new_dpt.min() - 0.5, new_dpt.max() + 0.5, 300)
if best_new[0] == "Linear":
    z_dense = m * dpt_dense + q
elif best_new[0] == "Inverse":
    z_dense = inverse(dpt_dense, a, b, c)
elif best_new[0] == "Quadratic":
    z_dense = np.polyval(p_quad, dpt_dense)
else:
    z_dense = np.polyval(p_cubic, dpt_dense)
ax.plot(dpt_dense, z_dense, color="tab:orange", lw=2, label=f"{best_new[0]} (R²={best_new[1]:.4f})")
ax.set_xlabel("Diopter [dpt]")
ax.set_ylabel("Z [m]")
ax.set_title("NEW Calibration (10 points)")
ax.legend()
ax.grid(True, alpha=0.3)

# Old calibration with both models
ax = axes[1]
ax.scatter(old_dpt, old_z, s=50, color="tab:green", zorder=3, label="Old data")
dpt_dense_old = np.linspace(old_dpt.min() - 0.5, old_dpt.max() + 0.5, 300)
z_old_lin = m_old * dpt_dense_old + q_old
ax.plot(dpt_dense_old, z_old_lin, color="tab:red", lw=2, label=f"Linear (R²={old_r2_lin:.4f})")

# Overlay best model from new data
if best_new[0] == "Linear":
    z_old_best = m * dpt_dense_old + q
elif best_new[0] == "Inverse":
    z_old_best = inverse(dpt_dense_old, a, b, c)
elif best_new[0] == "Quadratic":
    z_old_best = np.polyval(p_quad, dpt_dense_old)
else:
    z_old_best = np.polyval(p_cubic, dpt_dense_old)
ax.plot(dpt_dense_old, z_old_best, color="tab:orange", lw=2, linestyle="--",
        label=f"{best_new[0]} from new (R²={old_r2:.4f})")
ax.set_xlabel("Diopter [dpt]")
ax.set_ylabel("Z [m]")
ax.set_title("OLD Calibration (15 points)")
ax.legend()
ax.grid(True, alpha=0.3)

plt.tight_layout()
plt.show()
