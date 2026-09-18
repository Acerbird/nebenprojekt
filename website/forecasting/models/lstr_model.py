import json
import numpy as np
import pandas as pd
import datetime as dt
import statsmodels.api as sm
from scipy.optimize import minimize
from sklearn.metrics import mean_absolute_error

from models.linear_regression import _validate_and_prepare, _apply_lr_features, _hourly_setup


def lstr_loss(params, X, y, z):
    """
    lstr_loss computes the residual sum of squares for the LSTR(1) model given parameter vector params.
    Model: y = X @ beta0 + (X @ beta1) * G(z; gamma, c), where G is the logistic transition function.

    :param params: 1-D array of stacked parameters [beta0 (n_reg,), beta1 (n_reg,), gamma, c].
    :param X: Feature matrix of shape (n_obs, n_reg).
    :param y: Dependent variable array of shape (n_obs,).
    :param z: Transition variable array of shape (n_obs,), assumed to be normalized.
    :return: Scalar residual sum of squares.
    """
    n_reg = X.shape[1]
    beta0 = params[:n_reg]
    beta1 = params[n_reg:2*n_reg]
    gamma = params[-2]
    c = params[-1]
    G = 1 / (1 + np.exp(-gamma * (z - c)))
    y_pred = X @ beta0 + (X @ beta1) * G
    return np.sum((y - y_pred)**2)


def _normalize_z(X, z_name):
    """
    Extracts and standardizes the transition variable from the feature matrix X.
    Shared between fit_lstr and robust_lstr_fit.

    :param X: Feature DataFrame; must contain column z_name.
    :param z_name: Column name of the transition variable.
    :return: Standardized transition variable array of shape (n_obs,).
    """
    z = X[z_name].values
    return (z - z.mean()) / z.std()


def _lstr_starting_values(X, y, n_reg):
    """
    Builds the initial parameter vector and bounds for the single-start LSTR optimization.
    Uses OLS coefficients as the starting beta and zeros for the regime-difference term.

    :param X: Feature DataFrame of shape (n_obs, n_reg).
    :param y: Target variable array.
    :param n_reg: Number of regressors.
    :return: Tuple (x0, bounds) for scipy.optimize.minimize.
    """
    ols = sm.OLS(y, sm.add_constant(X)).fit()
    beta0_start = ols.params[1:]
    beta1_start = np.zeros(n_reg)
    x0 = np.concatenate([beta0_start, beta1_start, [1.0, 0.0]])
    bounds = [(-10, 10)] * (2 * n_reg) + [(0.01, None), (-5, 5)]
    return x0, bounds


def _run_lstr_optimization(X, y, z, x0, bounds):
    """
    Runs the L-BFGS-B minimization for the LSTR loss.

    :param X: Feature DataFrame.
    :param y: Target variable.
    :param z: Normalized transition variable.
    :param x0: Initial parameter vector.
    :param bounds: Parameter bounds list.
    :return: scipy OptimizeResult.
    """
    return minimize(lstr_loss, x0, args=(X, y, z), method='L-BFGS-B', bounds=bounds)


def _conditional_ols_stats(X_aug, y, n_reg, n_blocks):
    """
    Computes standard errors, t-values, and p-values for the regime coefficients of a
    fitted LSTR model by running OLS on the augmented design matrix with the transition
    parameters (gamma, c) held fixed at their estimated values. Since G = G(gamma, c) is
    treated as a known regressor, y = X_aug @ beta + e is a linear model and ordinary OLS
    inference applies directly to beta0/beta1(/beta2). This is the standard "conditional"
    approach for assessing coefficient significance in STR models; the uncertainty of
    gamma and c themselves is not propagated, so the resulting standard errors are
    slightly optimistic.

    :param X_aug: Augmented design matrix of shape (n_obs, n_blocks * n_reg), with column
                  blocks ordered as [X, X*G1, X*G2, ...] matching the beta blocks.
    :param y: Target variable array (n_obs,).
    :param n_reg: Number of regressors per block (columns in X).
    :param n_blocks: Number of beta blocks (2 for LSTR(1): beta0/beta1; 3 for LSTR(2):
                     beta0/beta1/beta2).
    :return: Tuple (se_blocks, tvalue_blocks, pvalue_blocks), each a tuple of n_blocks
             arrays of shape (n_reg,) giving the standard error, t-value, and p-value of
             each coefficient in beta0, beta1, (beta2).
    """
    ols = sm.OLS(y, X_aug).fit()
    se = np.asarray(ols.bse)
    tvalues = np.asarray(ols.tvalues)
    pvalues = np.asarray(ols.pvalues)
    se_blocks = tuple(se[i * n_reg:(i + 1) * n_reg] for i in range(n_blocks))
    tvalue_blocks = tuple(tvalues[i * n_reg:(i + 1) * n_reg] for i in range(n_blocks))
    pvalue_blocks = tuple(pvalues[i * n_reg:(i + 1) * n_reg] for i in range(n_blocks))
    return se_blocks, tvalue_blocks, pvalue_blocks


def _extract_lstr_results(X, y, z, result, n_reg):
    """
    Extracts parameters and computes metrics from a successful LSTR optimization result.
    Standard errors, t-values, and p-values for beta0/beta1 are computed via
    _conditional_ols_stats, treating the fitted gamma/c as fixed (see that function's
    docstring for the interpretation and limitations).

    :param X: Feature DataFrame used for prediction.
    :param y: Target variable.
    :param z: Normalized transition variable.
    :param result: scipy OptimizeResult from _run_lstr_optimization.
    :param n_reg: Number of regressors.
    :return: Results dict with keys "success", "beta0", "beta1", "beta0_se", "beta1_se",
             "beta0_tvalues", "beta1_tvalues", "beta0_pvalues", "beta1_pvalues", "gamma",
             "c", "G_mean", "y_pred", "regime_effects_mean", "mae", "r2", or failure dict.
    """
    if not result.success:
        return {'success': False, 'message': result.message}
    beta0 = result.x[:n_reg]
    beta1 = result.x[n_reg:2*n_reg]
    gamma = result.x[-2]
    c = result.x[-1]
    G = 1 / (1 + np.exp(-gamma * (z - c)))
    y_pred = X @ beta0 + (X @ beta1) * G
    regime_effects = beta0 + np.outer(G, beta1)  # shape (n_obs, n_reg)
    X_np = X.values if hasattr(X, "values") else np.asarray(X)
    X_aug = np.hstack([X_np, X_np * G[:, None]])
    (beta0_se, beta1_se), (beta0_tvalues, beta1_tvalues), (beta0_pvalues, beta1_pvalues) = \
        _conditional_ols_stats(X_aug, y, n_reg, 2)
    return {
        'success': True,
        'beta0': beta0,
        'beta1': beta1,
        'beta0_se': beta0_se,
        'beta1_se': beta1_se,
        'beta0_tvalues': beta0_tvalues,
        'beta1_tvalues': beta1_tvalues,
        'beta0_pvalues': beta0_pvalues,
        'beta1_pvalues': beta1_pvalues,
        'gamma': gamma,
        'c': c,
        'G_mean': G.mean(),
        'y_pred': y_pred,
        'regime_effects_mean': regime_effects.mean(axis=0),
        'mae': mean_absolute_error(y, y_pred),
        'r2': 1 - np.sum((y - y_pred)**2) / np.sum((y - np.mean(y))**2)
    }


def _concentrated_grid_search_lstr1(X_np, y, z, gamma_grid, c_grid):
    """
    For each (gamma, c) pair in the Cartesian grid, concentrates out (beta0, beta1) via OLS
    and returns the transition parameters and betas achieving the lowest RSS.

    Given fixed gamma and c, G = sigmoid(gamma * (z - c)) is a known vector, so
    y = [X, X*G] @ [beta0; beta1] is a standard linear model solved analytically.
    This reduces the non-convex search to a 2-D grid over (gamma, c) only.

    :param X_np: Feature matrix (n, n_reg) as numpy array.
    :param y: Target variable array (n,).
    :param z: Normalized transition variable (n,).
    :param gamma_grid: List of gamma values to search over.
    :param c_grid: List of threshold values to search over.
    :return: Tuple (best_tp, best_betas, best_rss) where best_tp = (gamma, c) and
             best_betas = stacked [beta0, beta1] of shape (2*n_reg,).
             Returns (None, None, inf) if no valid point is found.
    """
    best_rss, best_tp, best_betas = np.inf, None, None
    for gamma in gamma_grid:
        for c in c_grid:
            G = 1 / (1 + np.exp(-gamma * (z - c)))
            X_aug = np.hstack([X_np, X_np * G[:, None]])
            try:
                betas, _, _, _ = np.linalg.lstsq(X_aug, y, rcond=None)
            except np.linalg.LinAlgError:
                continue
            rss = float(np.sum((y - X_aug @ betas) ** 2))
            if rss < best_rss:
                best_rss = rss
                best_tp = (gamma, c)
                best_betas = betas
    return best_tp, best_betas, best_rss


def _fit_lstr1_concentrated(X_np, y, z, gamma_grid=None, c_grid=None,
                              warm_start=None, maxiter=350):
    """
    Fits LSTR(1) using concentrated least squares for the grid search followed by a single
    L-BFGS-B polishing step. For each (gamma, c) on the grid, betas are solved analytically,
    so only the 2-D (gamma, c) space needs to be explored. The best grid point serves as the
    starting value for one L-BFGS-B refinement step.

    When warm_start is a successful result dict from a previous day, the grid search is skipped
    and the previous parameters serve directly as the starting point for polishing.

    :param X_np: Feature matrix (n, n_reg) as numpy array.
    :param y: Target variable array (n,).
    :param z: Normalized transition variable (n,).
    :param gamma_grid: List of gamma values for the grid search.
                       Default: [0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0].
    :param c_grid: List of threshold values for the grid search.
                   Default: percentiles [5, 15, 25, 35, 50, 65, 75, 85, 95] of z.
    :param warm_start: Optional result dict from a previous fit (keys beta0, beta1, gamma, c
                       and success=True). Skips grid search when provided.
    :param maxiter: Maximum L-BFGS-B iterations for the polishing step (default 500).
    :return: Dict with keys success, beta0, beta1, beta0_se, beta1_se, beta0_tvalues,
             beta1_tvalues, beta0_pvalues, beta1_pvalues, gamma, c, G_mean, y_pred,
             regime_effects_mean, mae, r2, or failure dict with success=False. The
             SE/t-value/p-value arrays come from _conditional_ols_stats, treating the
             fitted gamma/c as fixed.
    """
    n_reg = X_np.shape[1]
    if gamma_grid is None:
        gamma_grid = [0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 5.0, 10.0]
    if c_grid is None:
        c_grid = list(np.percentile(z, [5, 15, 25, 35, 50, 65, 75, 85, 95]))

    if warm_start is not None and warm_start.get("success", False):
        ws = warm_start
        x0 = np.hstack([ws["beta0"], ws["beta1"], [ws["gamma"], ws["c"]]])
    else:
        best_tp, best_betas, _ = _concentrated_grid_search_lstr1(X_np, y, z, gamma_grid, c_grid)
        if best_tp is None:
            return {"success": False, "message": "Grid search found no valid point"}
        gamma0, c0 = best_tp
        x0 = np.hstack([best_betas, [gamma0, c0]])

    rss_before = lstr_loss(x0, X_np, y, z)
    bounds = [(-20, 20)] * (2 * n_reg) + [(0.01, 50), (-5, 5)]
    result = minimize(lstr_loss, x0, args=(X_np, y, z),
                      method="L-BFGS-B", bounds=bounds,
                      options={"maxiter": maxiter, "ftol": 1e-8, "gtol": 1e-8})

    best_params = result.x if result.fun < rss_before else x0
    beta0 = best_params[:n_reg]
    beta1 = best_params[n_reg:2 * n_reg]
    gamma = best_params[-2]
    c = best_params[-1]
    G = 1 / (1 + np.exp(-gamma * (z - c)))
    y_pred = X_np @ beta0 + (X_np @ beta1) * G
    regime_effects = beta0 + np.outer(G, beta1)
    X_aug = np.hstack([X_np, X_np * G[:, None]])
    (beta0_se, beta1_se), (beta0_tvalues, beta1_tvalues), (beta0_pvalues, beta1_pvalues) = \
        _conditional_ols_stats(X_aug, y, n_reg, 2)
    return {
        "success": True,
        "beta0": beta0, "beta1": beta1,
        "beta0_se": beta0_se, "beta1_se": beta1_se,
        "beta0_tvalues": beta0_tvalues, "beta1_tvalues": beta1_tvalues,
        "beta0_pvalues": beta0_pvalues, "beta1_pvalues": beta1_pvalues,
        "gamma": gamma, "c": c,
        "G_mean": float(G.mean()),
        "y_pred": y_pred,
        "regime_effects_mean": regime_effects.mean(axis=0),
        "mae": mean_absolute_error(y, y_pred),
        "r2": 1 - np.sum((y - y_pred) ** 2) / np.sum((y - np.mean(y)) ** 2),
    }


def fit_lstr(X, y, z_name="Residual Load"):
    """
    fit_lstr fits a Logistic Smooth Transition Regression (LSTR) model to the training data.
    The transition variable is taken from column z_name of X and internally normalized.

    :param X: Feature DataFrame of shape (n_obs, n_reg); must contain column z_name.
    :param y: Dependent variable array or Series of shape (n_obs,).
    :param z_name: Column name in X to use as the transition variable (default "Residual Load").
    :return: Dictionary with keys "success", "beta0", "beta1", "beta0_se", "beta1_se",
             "beta0_tvalues", "beta1_tvalues", "beta0_pvalues", "beta1_pvalues", "gamma",
             "c", "G_mean", "y_pred", "regime_effects_mean", "mae", "r2" on success, or
             "success", "message" on failure.
    """
    n_reg = X.shape[1]

    # data_import
    z = _normalize_z(X, z_name)

    # feature_building
    x0, bounds = _lstr_starting_values(X, y, n_reg)

    # fitting
    result = _run_lstr_optimization(X, y, z, x0, bounds)

    # predicting
    return _extract_lstr_results(X, y, z, result, n_reg)


def robust_lstr_fit(X, y, z_name="Residual Load", maxiter=500, warm_start=None):
    """
    robust_lstr_fit fits an LSTR model using concentrated least squares: for each (gamma, c)
    on a 2-D grid, betas are solved analytically via OLS, eliminating the need to run a full
    gradient optimizer at every starting point. The best grid solution is then polished with
    a single L-BFGS-B run. This explores a larger (gamma, c) space at lower cost than the
    previous multi-start gradient approach.

    :param X: Feature DataFrame of shape (n_obs, n_reg); must contain column z_name.
    :param y: Dependent variable array or Series of shape (n_obs,).
    :param z_name: Column name in X to use as the transition variable (default "Residual Load").
    :param maxiter: Maximum number of L-BFGS-B iterations for the polishing step (default 500).
    :param warm_start: Optional result dict from a previous fit (keys beta0, beta1, gamma, c and
                       success=True). When provided, the grid search is skipped and only one
                       L-BFGS-B polish is run from the previous parameters.
    :return: Dictionary with keys "success", "beta0", "beta1", "beta0_se", "beta1_se",
             "beta0_tvalues", "beta1_tvalues", "beta0_pvalues", "beta1_pvalues", "gamma",
             "c", "G_mean", "y_pred", "regime_effects_mean", "mae", "r2", "loss",
             "mae_str", "r2_str", "mae_linear", "r2_linear", "G" on success, or
             "success", "message", "fallback" on failure.
    """
    X_np = X.values if hasattr(X, "values") else np.asarray(X)
    z = _normalize_z(X, z_name)
    result = _fit_lstr1_concentrated(X_np, y, z, warm_start=warm_start, maxiter=maxiter)
    if not result["success"]:
        print("Concentrated LS failed -> Linear fallback")
        ols = sm.OLS(y, sm.add_constant(X)).fit()
        return {
            "success": False,
            "message": result.get("message", "Optimization failed"),
            "fallback": "linear",
            "mae_linear": mean_absolute_error(y, ols.fittedvalues),
            "r2_linear": ols.rsquared,
        }
    ols = sm.OLS(y, sm.add_constant(X)).fit()
    G_vec = 1 / (1 + np.exp(-result["gamma"] * (z - result["c"])))
    return {
        **result,
        "loss": float(np.sum((y - result["y_pred"]) ** 2)),
        "mae_str": result["mae"],
        "r2_str": result["r2"],
        "mae_linear": mean_absolute_error(y, ols.fittedvalues),
        "r2_linear": ols.rsquared,
        "G": G_vec,
    }


###########################################################################################################
### --- FORECAST MODEL --- ###
###########################################################################################################


def _is_first_workday_of_month(date: dt.datetime) -> bool:
    """
    Returns True if date is the first Monday-Friday of its calendar month.

    :param date: The date to check.
    :return: True if date is the first workday of its month, False otherwise.
    """
    first = date.replace(day=1)
    while first.weekday() >= 5:
        first += dt.timedelta(days=1)
    return date.day == first.day


def _is_dummy_col(col_name: str) -> bool:
    """
    Returns True for binary/constant columns that should be excluded from z-score normalization.

    :param col_name: Column name string from the design matrix.
    :return: True if the column is a dummy or intercept and should not be standardized.
    """
    return (
        col_name == "Intercept"
        or col_name.startswith("Hour_")
        or col_name.startswith("Month_")
        or col_name.startswith("Weekday_")
        or col_name == "Workday"
    )


def _fit_lstr_external_z(X, y, z_normalized, use_robust=False, warm_start=None, maxiter=500):
    """
    _fit_lstr_external_z fits an LSTR model where z is provided externally (already normalized)
    and is NOT a column of X. Used when z_as_feature=False so the optimizer sees only the non-z
    regressors, while z still drives the transition function.

    :param X: Feature DataFrame of shape (n_obs, n_reg) — must NOT contain the z column.
    :param y: Dependent variable array or Series of shape (n_obs,).
    :param z_normalized: Pre-normalized transition variable array (n_obs,).
    :param use_robust: If True, use concentrated LS grid search when no warm_start is available.
                       Default False.
    :param warm_start: Optional result dict from a previous fit (keys beta0, beta1, gamma, c and
                       success=True). When provided, the grid search is skipped entirely and only
                       one L-BFGS-B polish is run, regardless of use_robust.
    :param maxiter: Maximum L-BFGS-B iterations for the polishing step (only used when use_robust
                    or warm_start is provided).
    :return: Same result dict as fit_lstr or _fit_lstr1_concentrated.
    """
    X_np = X.values if hasattr(X, "values") else np.asarray(X)
    return _fit_lstr1_concentrated(X_np, y, z_normalized, warm_start=warm_start, maxiter=maxiter)


def _predict_lstr_params(X_test_np, z_test_raw, beta0, beta1, gamma, c, z_train_mean, z_train_std):
    """
    _predict_lstr_params applies fitted LSTR parameters to out-of-sample data.
    Normalizes the test transition variable using training statistics so the scale matches
    what the optimizer saw during fitting.

    :param X_test_np: Test feature matrix as a 2-D numpy array (n_test, n_reg), with columns
                      in the same order as the training feature matrix passed to fit_lstr.
    :param z_test_raw: Unnormalized test transition variable array (n_test,).
    :param beta0: Linear regime coefficient vector (n_reg,) from a successful fit.
    :param beta1: Nonlinear regime difference vector (n_reg,) from a successful fit.
    :param gamma: Transition speed parameter from the fit.
    :param c: Transition location parameter (on normalized scale) from the fit.
    :param z_train_mean: Mean of the training transition variable, used for normalization.
    :param z_train_std: Standard deviation of the training transition variable.
    :return: Predicted values array of shape (n_test,).
    """
    z_norm = (z_test_raw - z_train_mean) / z_train_std
    G = 1 / (1 + np.exp(-gamma * (z_norm - c)))
    return X_test_np @ beta0 + (X_test_np @ beta1) * G


# ─── LSTR(2) additive model helpers ──────────────────────────────────────────

def _g2_gate_factor(G1, G2, a1, a2, multiply_g1=False, g2_gate="product", softmin_k=4.0):
    """
    Computes the multiplier applied to (X @ beta2) in the LSTR(2) model, i.e. how the fuel-cost
    regime term is gated by the quantity-stress transition G1. Shared by the loss, grid search,
    result extraction, and out-of-sample prediction so all four stay consistent.

    :param G1: First transition function values sigmoid(a1), shape (n,).
    :param G2: Second transition function values sigmoid(a2), shape (n,).
    :param a1: Logit argument of G1, gamma1 * (z1 - c1), shape (n,).
    :param a2: Logit argument of G2, gamma2 * (z2 - c2), shape (n,).
    :param multiply_g1: If False, returns G2 unchanged (plain additive LSTR(2)). Default False.
    :param g2_gate: Gating formula used when multiply_g1=True. "product" (default) multiplies
                    G1 * G2 — a hard AND that requires both sigmoids to be near-simultaneously
                    saturated before the regime activates. "softmin" instead applies the sigmoid
                    to a smooth minimum of a1 and a2, i.e. sigmoid(softmin_k(a1, a2)), which
                    converges to min(G1, G2) as softmin_k -> inf (since sigmoid is monotonic).
                    This is a softer AND: it activates once the more binding of the two
                    conditions is met, without needing both margins deep in saturation, so it
                    should populate the joint regime more readily than "product" when G1 and G2
                    are each only moderately elevated.
    :param softmin_k: Hardness of the smooth minimum used when g2_gate="softmin"; larger values
                      approach the true elementwise minimum more closely (default 4.0).
    :return: Gate multiplier array, shape (n,).
    """
    if not multiply_g1:
        return G2
    if g2_gate == "softmin":
        soft_min_a = -np.logaddexp(-softmin_k * a1, -softmin_k * a2) / softmin_k
        return 1 / (1 + np.exp(-soft_min_a))
    return G1 * G2


def lstr2_loss(params, X, y, z1, z2, multiply_g1=False, g2_gate="product", softmin_k=4.0):
    """
    lstr2_loss computes the RSS for an LSTR(2) model.
    Additive:  y = X @ beta0 + (X @ beta1) * G1 + (X @ beta2) * G2
    Nested:    y = X @ beta0 + (X @ beta1) * G1 + (X @ beta2) * g2_factor  (multiply_g1=True),
               where g2_factor is G1*G2 or the softmin-gated alternative; see _g2_gate_factor.

    :param params: Stacked parameter vector [beta0 (n_reg), beta1 (n_reg), beta2 (n_reg),
                   gamma1, c1, gamma2, c2].
    :param X: Feature matrix (n, n_reg) as numpy array.
    :param y: Target variable array (n,).
    :param z1: Normalized first transition variable (n,).
    :param z2: Normalized second transition variable (n,).
    :param multiply_g1: If True, gates the second transition term by G1 (see _g2_gate_factor).
                        Default False (additive form).
    :param g2_gate: Gating formula when multiply_g1=True — "product" or "softmin"; see
                    _g2_gate_factor. Default "product".
    :param softmin_k: Hardness of the smooth minimum when g2_gate="softmin" (default 4.0).
    :return: Scalar RSS.
    """
    n_reg = X.shape[1]
    beta0 = params[:n_reg]
    beta1 = params[n_reg:2 * n_reg]
    beta2 = params[2 * n_reg:3 * n_reg]
    gamma1, c1, gamma2, c2 = params[-4], params[-3], params[-2], params[-1]
    a1 = gamma1 * (z1 - c1)
    a2 = gamma2 * (z2 - c2)
    G1 = 1 / (1 + np.exp(-a1))
    G2 = 1 / (1 + np.exp(-a2))
    g2_factor = _g2_gate_factor(G1, G2, a1, a2, multiply_g1, g2_gate, softmin_k)
    y_pred = X @ beta0 + (X @ beta1) * G1 + (X @ beta2) * g2_factor
    return np.sum((y - y_pred) ** 2)


def _concentrated_grid_search(X_np, y, z1, z2, gamma_grid, c1_grid, c2_grid, multiply_g1=False,
                               g2_gate="product", softmin_k=4.0):
    """
    For each (gamma1, c1, gamma2, c2) in the Cartesian grid, concentrates out
    (beta0, beta1, beta2) via OLS and returns the transition parameters and betas
    achieving the lowest RSS. When z1 and z2 are identical, enforces c1 <= c2 to
    break permutation symmetry and halve the search space.

    :param X_np: Feature matrix (n, n_reg).
    :param y: Target variable (n,).
    :param z1: Normalized first transition variable (n,).
    :param z2: Normalized second transition variable (n,).
    :param gamma_grid: List of gamma values to search over.
    :param c1_grid: List of threshold values for z1.
    :param c2_grid: List of threshold values for z2. When z1 == z2, pass c1_grid here as well
                    so that the symmetry-breaking condition c1 <= c2 is applied correctly.
    :param multiply_g1: If True, the third augmented column is gated by G1 (nested form); see
                        _g2_gate_factor for the "product" vs "softmin" gating formula.
    :param g2_gate: Gating formula when multiply_g1=True — "product" or "softmin"; see
                    _g2_gate_factor. Default "product".
    :param softmin_k: Hardness of the smooth minimum when g2_gate="softmin" (default 4.0).
    :return: Tuple (best_transition_params, best_betas, best_rss) where
             best_transition_params = (gamma1, c1, gamma2, c2) and
             best_betas = stacked [beta0, beta1, beta2] of shape (3*n_reg,).
             Returns (None, None, inf) if no valid point is found.
    """
    same_z = np.allclose(z1, z2)
    best_rss, best_tp, best_betas = np.inf, None, None

    for gamma1 in gamma_grid:
        for c1 in c1_grid:
            a1 = gamma1 * (z1 - c1)
            G1 = 1 / (1 + np.exp(-a1))
            for gamma2 in gamma_grid:
                for c2 in c2_grid:
                    if same_z and c1 > c2:
                        continue
                    a2 = gamma2 * (z2 - c2)
                    G2 = 1 / (1 + np.exp(-a2))
                    g2_factor = _g2_gate_factor(G1, G2, a1, a2, multiply_g1, g2_gate, softmin_k)
                    X_aug = np.hstack([X_np, X_np * G1[:, None], X_np * g2_factor[:, None]])
                    try:
                        betas, _, _, _ = np.linalg.lstsq(X_aug, y, rcond=None)
                    except np.linalg.LinAlgError:
                        continue
                    rss = float(np.sum((y - X_aug @ betas) ** 2))
                    if rss < best_rss:
                        best_rss = rss
                        best_tp = (gamma1, c1, gamma2, c2)
                        best_betas = betas

    return best_tp, best_betas, best_rss


def _extract_lstr2_results(X_np, y, z1, z2, params, n_reg, multiply_g1=False,
                            g2_gate="product", softmin_k=4.0):
    """
    Extracts parameters and computes in-sample metrics from an LSTR(2) parameter vector.

    :param X_np: Feature matrix (n, n_reg).
    :param y: Target variable (n,).
    :param z1: Normalized first transition variable (n,).
    :param z2: Normalized second transition variable (n,).
    :param params: Parameter vector [beta0, beta1, beta2, gamma1, c1, gamma2, c2].
    :param n_reg: Number of regressors.
    :param multiply_g1: If True, uses the nested prediction form (X @ beta2) * g2_factor; see
                        _g2_gate_factor for the "product" vs "softmin" gating formula.
    :param g2_gate: Gating formula when multiply_g1=True — "product" or "softmin"; see
                    _g2_gate_factor. Default "product".
    :param softmin_k: Hardness of the smooth minimum when g2_gate="softmin" (default 4.0).
    :return: Dict with keys success, beta0, beta1, beta2, beta0_se, beta1_se, beta2_se,
             beta0_tvalues, beta1_tvalues, beta2_tvalues, beta0_pvalues, beta1_pvalues,
             beta2_pvalues, gamma1, c1, gamma2, c2, G1_mean, G2_mean, y_pred, mae, r2.
             The SE/t-value/p-value arrays come from _conditional_ols_stats, treating the
             fitted gamma1/c1/gamma2/c2 as fixed.
    """
    beta0 = params[:n_reg]
    beta1 = params[n_reg:2 * n_reg]
    beta2 = params[2 * n_reg:3 * n_reg]
    gamma1, c1, gamma2, c2 = params[-4], params[-3], params[-2], params[-1]
    a1 = gamma1 * (z1 - c1)
    a2 = gamma2 * (z2 - c2)
    G1 = 1 / (1 + np.exp(-a1))
    G2 = 1 / (1 + np.exp(-a2))
    g2_factor = _g2_gate_factor(G1, G2, a1, a2, multiply_g1, g2_gate, softmin_k)
    y_pred = X_np @ beta0 + (X_np @ beta1) * G1 + (X_np @ beta2) * g2_factor
    X_aug = np.hstack([X_np, X_np * G1[:, None], X_np * g2_factor[:, None]])
    (beta0_se, beta1_se, beta2_se), (beta0_tvalues, beta1_tvalues, beta2_tvalues), \
        (beta0_pvalues, beta1_pvalues, beta2_pvalues) = _conditional_ols_stats(X_aug, y, n_reg, 3)
    return {
        "success": True,
        "beta0": beta0, "beta1": beta1, "beta2": beta2,
        "beta0_se": beta0_se, "beta1_se": beta1_se, "beta2_se": beta2_se,
        "beta0_tvalues": beta0_tvalues, "beta1_tvalues": beta1_tvalues, "beta2_tvalues": beta2_tvalues,
        "beta0_pvalues": beta0_pvalues, "beta1_pvalues": beta1_pvalues, "beta2_pvalues": beta2_pvalues,
        "gamma1": gamma1, "c1": c1,
        "gamma2": gamma2, "c2": c2,
        "G1_mean": float(G1.mean()),
        "G2_mean": float(G2.mean()),
        "y_pred": y_pred,
        "mae": mean_absolute_error(y, y_pred),
        "r2": 1 - np.sum((y - y_pred) ** 2) / np.sum((y - np.mean(y)) ** 2),
    }


def _fit_lstr2_concentrated(X_np, y, z1, z2, gamma_grid=None, c1_grid=None, c2_grid=None,
                             warm_start=None, maxiter=350, multiply_g1=False,
                             g2_gate="product", softmin_k=4.0):
    """
    Fits an LSTR(2) model using concentrated least squares for the grid search followed by a
    single L-BFGS-B polishing step. This is substantially faster than running a full gradient
    optimizer for every grid point.

    When warm_start is a successful result dict from a previous day, the grid search is
    skipped and the previous parameters serve directly as the starting point for polishing.
    This enables efficient rolling-window forecasting.

    :param X_np: Feature matrix (n, n_reg) as numpy array.
    :param y: Target variable (n,).
    :param z1: Normalized first transition variable (n,).
    :param z2: Normalized second transition variable (n,). May equal z1.
    :param gamma_grid: List of gamma values for the grid search.
                       Default: [0.5, 1.0, 1.5, 2.0, 3.0, 5.0, 10.0] — 7 values, finer in [1, 3].
    :param c1_grid: Threshold values for z1. Default: percentiles [10, 25, 50, 75, 90] of z1.
    :param c2_grid: Threshold values for z2. Default: percentiles [15, 35, 55, 75, 90, 95] of z2
                    (shifted right vs c1_grid to avoid ceiling effects on right-skewed variables).
                    When z1 == z2, c2_grid is set equal to c1_grid automatically.
    :param warm_start: Optional result dict from a previous fit (keys beta0, beta1, beta2,
                       gamma1, c1, gamma2, c2 and success=True). Skips grid search when provided.
    :param maxiter: Maximum L-BFGS-B iterations for the polishing step (default 500).
    :param multiply_g1: If True, fits the nested form y = X@beta0 + (X@beta1)*G1 + (X@beta2)*g2_factor
                        instead of the additive form. Default False.
    :param g2_gate: Gating formula when multiply_g1=True. "product" (default) is the original
                    G1*G2 nested form — a hard AND requiring both transition functions near
                    saturation. "softmin" instead gates on sigmoid(softmin_k(a1, a2)), which
                    approaches min(G1, G2) as softmin_k grows — a softer AND that activates once
                    the more binding of the two conditions is met; see _g2_gate_factor.
    :param softmin_k: Hardness of the smooth minimum when g2_gate="softmin"; larger values track
                      the true elementwise minimum more closely (default 4.0).
    :return: Dict with keys success, beta0, beta1, beta2, beta0_se, beta1_se, beta2_se,
             beta0_tvalues, beta1_tvalues, beta2_tvalues, beta0_pvalues, beta1_pvalues,
             beta2_pvalues, gamma1, c1, gamma2, c2, G1_mean, G2_mean, y_pred, mae, r2, or
             failure dict with success=False.
    """
    n_reg = X_np.shape[1]

    if gamma_grid is None:
        gamma_grid = [0.5, 1.0, 1.5, 2.0, 3.0, 5.0, 10.0]
    if c1_grid is None:
        c1_grid = list(np.percentile(z1, [5, 15, 25, 35, 50, 65, 75, 85, 95]))
    if c2_grid is None:
        c2_grid = c1_grid if np.allclose(z1, z2) else list(np.percentile(z2, [5, 15, 25, 35, 50, 65, 75, 85, 93, 97]))

    if warm_start is not None and warm_start.get("success", False):
        ws = warm_start
        x0 = np.hstack([ws["beta0"], ws["beta1"], ws["beta2"],
                        [ws["gamma1"], ws["c1"], ws["gamma2"], ws["c2"]]])
    else:
        best_tp, best_betas, _ = _concentrated_grid_search(
            X_np, y, z1, z2, gamma_grid, c1_grid, c2_grid, multiply_g1=multiply_g1,
            g2_gate=g2_gate, softmin_k=softmin_k
        )
        if best_tp is None:
            return {"success": False, "message": "Grid search found no valid point"}
        gamma1_0, c1_0, gamma2_0, c2_0 = best_tp
        x0 = np.hstack([best_betas, [gamma1_0, c1_0, gamma2_0, c2_0]])

    rss_before = lstr2_loss(x0, X_np, y, z1, z2, multiply_g1, g2_gate, softmin_k)
    bounds = [(-20, 20)] * (3 * n_reg) + [(0.01, 50), (-5, 5), (0.01, 50), (-5, 5)]
    result = minimize(lstr2_loss, x0, args=(X_np, y, z1, z2, multiply_g1, g2_gate, softmin_k),
                      method="L-BFGS-B",
                      bounds=bounds, options={"maxiter": maxiter, "ftol": 1e-8, "gtol": 1e-8})

    best_params = result.x if result.fun < rss_before else x0
    return _extract_lstr2_results(X_np, y, z1, z2, best_params, n_reg, multiply_g1,
                                   g2_gate=g2_gate, softmin_k=softmin_k)


def _predict_lstr2_params(X_test_np, z1_test_raw, z2_test_raw,
                           beta0, beta1, beta2,
                           gamma1, c1, gamma2, c2,
                           z1_train_mean, z1_train_std,
                           z2_train_mean, z2_train_std,
                           multiply_g1=False, g2_gate="product", softmin_k=4.0):
    """
    Applies fitted LSTR(2) parameters to out-of-sample data, normalizing the transition
    variables using training statistics so the scale matches what the optimizer saw.

    :param X_test_np: Test feature matrix (n_test, n_reg).
    :param z1_test_raw: Unnormalized first transition variable (n_test,).
    :param z2_test_raw: Unnormalized second transition variable (n_test,).
    :param beta0, beta1, beta2: Regime coefficients from the fit.
    :param gamma1, c1, gamma2, c2: Transition parameters from the fit.
    :param z1_train_mean, z1_train_std: Training mean and std for z1 normalization.
    :param z2_train_mean, z2_train_std: Training mean and std for z2 normalization.
    :param multiply_g1: If True, uses the nested form (X @ beta2) * g2_factor. Must match
                        the flag used during fitting. Default False.
    :param g2_gate: Gating formula when multiply_g1=True — "product" or "softmin"; see
                    _g2_gate_factor. Must match the value used during fitting. Default "product".
    :param softmin_k: Hardness of the smooth minimum when g2_gate="softmin". Must match the
                      value used during fitting (default 4.0).
    :return: Predicted values array (n_test,).
    """
    z1_norm = (z1_test_raw - z1_train_mean) / z1_train_std
    z2_norm = (z2_test_raw - z2_train_mean) / z2_train_std
    a1 = gamma1 * (z1_norm - c1)
    a2 = gamma2 * (z2_norm - c2)
    G1 = 1 / (1 + np.exp(-a1))
    G2 = 1 / (1 + np.exp(-a2))
    g2_factor = _g2_gate_factor(G1, G2, a1, a2, multiply_g1, g2_gate, softmin_k)
    return X_test_np @ beta0 + (X_test_np @ beta1) * G1 + (X_test_np @ beta2) * g2_factor


def feature_names_from_dict(config: dict, gran: int = 24) -> list:
    """
    feature_names_from_dict returns the ordered list of regressor names that lstr_hourly_forecast
    places in the design matrix X for the given feature configuration. The order matches the
    column order of the beta arrays stored by save_lstr_run, so each beta coefficient can be
    mapped to its feature by index.

    :param config: Configuration dict with keys "features_lstr", "z_name_lstr", "z_as_feature_lstr", "z2_name_lstr",
                   "z2_as_feature", and "add_intercept_lstr" controlling the inclusion of z and z2 as features.
    :param gran: Granularity — 24 for hourly, 96 for quarter-hourly (default 24). Controls the
                 timestep label in lag and change feature names.
    :return: List of feature name strings in the column order of the design matrix X.
    """
    features = config["features_lstr"]
    z_name = config["z_name_lstr"]
    z_as_feature = config["z_as_feature_lstr"]
    z2_name = config["z2_name_lstr"]
    z2_as_feature = config["z2_as_feature_lstr"]
    add_intercept = config.get("add_intercept_lstr", False)

    TIMESTEP = "Hour" if gran == 24 else "Quarter"
    HOURS = 24

    names = []
    if add_intercept:
        names.append("Intercept")
    if features.get("load_wind_solar"):
        names.extend(["Load", "Solar", "Wind Offshore", "Wind Onshore"])
    if features.get("residual_load"):
        names.append("Residual Load")
    if features.get("residual_load_lags"):
        names.extend([f"Residual Load {TIMESTEP}-{int(lag)}" for lag in features["residual_load_lags"]])
    if features.get("residual_load_lags_mean"):
        names.append("Residual Load Lags Mean")
    if features.get("residual_load_change"):
        names.extend([f"RL Change {TIMESTEP}-{int(lag)}" for lag in features["residual_load_change"]])
    if features.get("residual_load_share"):
        names.append("Residual Load Share")
    if features.get("residual_load_share_powers"):
        names.extend([f"Residual Load Share Power {p}" for p in features["residual_load_share_powers"]])
    if features.get("fuels_separate"):
        comms = ["Gas", "Coal", "CO2"] if features.get("no_oil") else ["Gas", "Coal", "Oil", "CO2"]
        names.extend([f"{comm} d-2" for comm in comms])
    if features.get("fuels_adjusted"):
        comms_adj = ["Gas Adj", "Coal Adj"] if features.get("no_oil") else ["Gas Adj", "Coal Adj", "Oil Adj"]
        names.extend([f"{comm} d-2" for comm in comms_adj])
    if features.get("fuels_combined"):
        names.append("Fuel Index")
    if features.get("imports_exports"):
        names.append("Net Imports")
    if features.get("hour_dummies"):
        names.extend([f"Hour_{i}" for i in range(HOURS)])
    if features.get("month_dummies"):
        names.extend([f"Month_{i}" for i in range(1, 13)])
    if features.get("weekday_dummies"):
        names.extend([f"Weekday_{i}" for i in range(7)])
    if features.get("workday_dummy"):
        names.append("Workday")
    if features.get("seasonality_continuous_dummy"):
        names.extend(["Seasonality Sin", "Seasonality Cos"])

    _z2_as_feature = z_as_feature if z2_as_feature is None else z2_as_feature
    if not z_as_feature and z_name in names:
        names.remove(z_name)
    if z2_name is not None and z2_name != z_name and not _z2_as_feature and z2_name in names:
        names.remove(z2_name)

    return names


def lstr_hourly_forecast(df: pd.DataFrame, forecast_day: dt.datetime, gran=24,
                         training_period=365, bidding_zone="Price", features: dict = None,
                         z_name="Residual Load", z_as_feature=False, use_robust=False,
                         z2_name=None, z2_as_feature=None, multiply_g1_into_g2: bool = False,
                         g2_gate: str = "product", softmin_k: float = 4.0,
                         z2_norm_window_days: int = 365,
                         exact_params=None, prev_params=None,
                         transform_target: bool = False, transform_target_method: str = "asinh",
                         c_asinh: float = 1, add_intercept: bool = True,
                         normalize_features: bool = False):
    """
    lstr_hourly_forecast predicts electricity prices for each delivery time of forecast_day by
    fitting a separate LSTR model per time-of-day. It uses the same feature set and column logic
    as lr_hourly_forecast: features are controlled via the features dict, and the transition
    variable z is the training column named by z_name.
    At test time the corresponding forecast column (same position in TEST_COLUMNS) is used for z.
    Falls back to OLS for any hour where the LSTR optimization fails.

    When z2_name is set, an additive LSTR(2) model is fitted instead of LSTR(1). The second
    transition variable may equal z_name (two thresholds on the same variable) or be a different
    column. LSTR(2) always uses the concentrated-least-squares grid search; use_robust is ignored.

    :param df: DataFrame with at minimum columns "Timestamp Berlin", bidding_zone, "Date", "Time",
               "Hour", "Weekday", "Year", "Load", "Load Forecast", "Solar",
               "Solar Generation Forecast", "Wind Offshore", "Wind Offshore Generation Forecast",
               "Wind Onshore", "Wind Onshore Generation Forecast", and fuel price MTF columns.
    :param forecast_day: Date for which the forecast shall be produced.
    :param gran: Granularity — 24 for hourly, 96 for quarter-hourly (default 24).
    :param training_period: Number of days used for model training (default 365).
    :param bidding_zone: Name of the price column in df (default "Price").
    :param features: Dictionary of boolean feature flags (same keys as lr_hourly_forecast).
                     Default enables residual_load, fuels_separate, and weekday_dummies.
                     The feature flag for z_name must always be True so that _apply_lr_features
                     computes the column (e.g. "residual_load": True when z_name="Residual Load").
    :param z_name: Training column name of the LSTR transition variable (default "Residual Load").
                   The corresponding feature flag must be True regardless of z_as_feature.
    :param z_as_feature: If True, z_name is also included as a regular linear regressor in the
                         design matrix X. If False (default), z drives only the transition function
                         and is excluded from X; the other selected features form X alone.
    :param use_robust: If True, use robust_lstr_fit (grid search, verbose) for LSTR(1); otherwise
                       use fit_lstr (single-start, faster). Ignored when z2_name is set.
    :param z2_name: Column name of the second transition variable for the additive LSTR(2) model.
                    Must be present in the selected features. When None (default), the LSTR(1)
                    model is used. Set to the same value as z_name to model two thresholds on the
                    same variable.
    :param z2_as_feature: Controls whether z2_name is also included as a regular linear regressor
                          in X. When None (default), inherits the value of z_as_feature. Set to
                          True to keep z2 as both a transition variable and a regressor while
                          z_as_feature=False excludes z1 from X. Ignored when z2_name is None or
                          z2_name == z_name.
    :param multiply_g1_into_g2: If True and z2_name is set, fits the nested LSTR(2) form
                                 y = X@beta0 + (X@beta1)*G1 + (X@beta2)*g2_factor so that the cost
                                 term is gated by the RL transition (g2_factor per g2_gate).
                                 Ignored when z2_name is None. Default False (standard additive
                                 form). Must be stored in the run config when using save_lstr_run
                                 to ensure consistent replay.
    :param g2_gate: Gating formula used when multiply_g1_into_g2=True. "product" (default) is
                    the original g2_factor = G1*G2 — a hard AND requiring both transition
                    functions near-simultaneously saturated. "softmin" instead sets
                    g2_factor = sigmoid(softmin_k(a1, a2)), approaching min(G1, G2) as softmin_k
                    grows — a softer AND that activates once the more binding of the RL/gas
                    conditions is met, intended to populate the fuel-cost regime more readily
                    than "product" without requiring both margins deep in saturation. Ignored
                    when multiply_g1_into_g2=False or z2_name is None.
    :param softmin_k: Hardness of the smooth minimum used when g2_gate="softmin"; larger values
                      track the true elementwise minimum more closely (default 4.0). Ignored
                      otherwise.
    :param z2_norm_window_days: Number of most-recent days (counting back from forecast_day)
                                used to compute the mean/std that normalize z2 before fitting.
                                Limiting this to a recent window prevents structural breaks far
                                back in the training period (e.g. the 2022 gas price spike) from
                                dominating the scale of z2, which would otherwise compress the
                                z2-values seen in more recent data and make G2 saturate too low.
                                Set to None to normalize using the full training window (previous
                                behavior). Ignored when z2_name is None. Default 365 (12 months).
    :param exact_params: Optional list of result dicts for the current forecast day (one per
                         time-of-day slot), as returned by a prior run and saved via
                         save_lstr_run. When a slot's dict has success=True, the optimization
                         is skipped entirely and the stored parameters are used directly for
                         prediction. Use this to replay a run without refitting. Slots with
                         success=False fall back to normal fitting.
    :param prev_params: Optional list of result dicts from the previous forecast day (the
                        lstr_results_list returned by a prior call with the same z2_name). When
                        provided, the grid search is skipped for each hour and the previous
                        parameters serve as warm start, which is much faster for rolling windows.
                        Must have the same length as the number of time-of-day slots.
    :param transform_target: If True, applies a monotone transformation to the price target
                             before fitting and its inverse after prediction. Compresses extreme
                             spikes and negative prices so they have less influence on the
                             optimizer (default False).
    :param transform_target_method: Transformation to apply — currently only "asinh" is supported.
                                    asinh(c * y) behaves like log for large values and is defined
                                    for zero and negative prices (default "asinh").
    :param c_asinh: Scaling constant inside asinh(c * y). c=1 keeps €/MWh scale roughly intact
                    for typical prices; increase to compress more aggressively (default 1).
    :param add_intercept: If True, prepends a column of ones to the design matrix so each regime
                          gets its own intercept term (default True). Set to False only when
                          replaying runs saved without an intercept.
    :param normalize_features: If True, z-score standardizes all continuous feature columns in the
                               design matrix using training-window statistics before fitting. Binary
                               dummy columns (Intercept, Hour_*, Month_*, Weekday_*, Workday) are
                               excluded from normalization. The same statistics are applied to test
                               features. Normalization statistics (X_norm_means, X_norm_stds) are
                               stored in each result dict and persisted by save_lstr_run for exact
                               replay (default False).
    :return: Tuple of (forecast_df, lstr_results_list) where forecast_df has columns
             "Timestamp Berlin", "LSTR Hourly Forecast", "Real Price" and lstr_results_list contains
             one result dict per time-of-day. Each successful result dict additionally includes
             beta{i}_se, beta{i}_tvalues, and beta{i}_pvalues — conditional standard errors,
             t-values, and p-values for the beta0/beta1(/beta2) coefficients, computed by
             _conditional_ols_stats with gamma/c (or gamma1/c1/gamma2/c2) held fixed at their
             fitted values. Pass lstr_results_list as prev_params on the next rolling-window
             call to enable warm-starting.
    """
    HOURS = 24
    TIMESTEP = "Hour" if gran == 24 else "Quarter"

    COLUMNS_NEEDED = [
        "Timestamp Berlin", bidding_zone, "Date", "Time", "Hour", "Weekday", "Year",
        "Load", "Load Forecast",
        "Solar", "Solar Generation Forecast",
        "Wind Offshore", "Wind Offshore Generation Forecast",
        "Wind Onshore", "Wind Onshore Generation Forecast",
        "Coal Price MTF", "Gas Price MTF", "Oil Price MTF", "CO2 Price MTF"
    ]
    if gran == 96:
        COLUMNS_NEEDED.append("Minutes")

    if features is None:
        features = {
            "load_wind_solar": True, "residual_load": True, "residual_load_lags": False,
            "residual_load_lags_mean": False, "residual_load_change": False,
            "residual_load_share": False, "residual_load_share_powers": False,
            "fuels_separate": True, "fuels_adjusted": False, "no_oil": True, "fuels_combined": False,
            "imports_exports": False, "hour_dummies": False, "month_dummies": False,
            "weekday_dummies": False, "workday_dummy": False, "seasonality_continuous_dummy": False
        }

    if prev_params is not None and forecast_day.month % 2 == 1 and _is_first_workday_of_month(forecast_day):
        prev_params = None

    if features["imports_exports"]:
        COLUMNS_NEEDED.append("Net Position Trade")

    TRAINING_COLUMNS = ["Timestamp Berlin", "Date", "Time", "Price"]
    TEST_COLUMNS = ["Timestamp Berlin", "Date", "Time", "Price"]

    if features["load_wind_solar"]:
        TRAINING_COLUMNS.extend(["Load", "Solar", "Wind Offshore", "Wind Onshore"])
        TEST_COLUMNS.extend(["Load Forecast", "Solar Generation Forecast",
                             "Wind Offshore Generation Forecast", "Wind Onshore Generation Forecast"])
    if features["residual_load"]:
        TRAINING_COLUMNS.append("Residual Load")
        TEST_COLUMNS.append("Residual Load Forecast")
    if features["residual_load_lags"]:
        TRAINING_COLUMNS.extend([f"Residual Load {TIMESTEP}-{int(lag)}" for lag in features["residual_load_lags"]])
        TEST_COLUMNS.extend([f"Residual Load Forecast {TIMESTEP}-{int(lag)}" for lag in features["residual_load_lags"]])
    if features["residual_load_lags_mean"]:
        TRAINING_COLUMNS.append("Residual Load Lags Mean")
        TEST_COLUMNS.append("Residual Load Forecast Lags Mean")
    if features["residual_load_change"]:
        TRAINING_COLUMNS.extend([f"RL Change {TIMESTEP}-{int(lag)}" for lag in features["residual_load_change"]])
        TEST_COLUMNS.extend([f"RL Forecast Change {TIMESTEP}-{int(lag)}" for lag in features["residual_load_change"]])
    if features["residual_load_share"]:
        TRAINING_COLUMNS.append("Residual Load Share")
        TEST_COLUMNS.append("Residual Load Forecast Share")
    if features["residual_load_share_powers"]:
        TRAINING_COLUMNS.extend([f"Residual Load Share Power {p}" for p in features["residual_load_share_powers"]])
        TEST_COLUMNS.extend([f"Residual Load Forecast Share Power {p}" for p in features["residual_load_share_powers"]])
    if features["fuels_separate"]:
        comms = ["Gas", "Coal", "CO2"] if features.get("no_oil", False) else ["Gas", "Coal", "Oil", "CO2"]
        TRAINING_COLUMNS.extend([f"{comm} d-2" for comm in comms])
        TEST_COLUMNS.extend([f"{comm} d-2" for comm in comms])
    if features.get("fuels_adjusted"):
        comms_adj = ["Gas Adj", "Coal Adj"] if features.get("no_oil", False) else ["Gas Adj", "Coal Adj", "Oil Adj"]
        TRAINING_COLUMNS.extend([f"{comm} d-2" for comm in comms_adj])
        TEST_COLUMNS.extend([f"{comm} d-2" for comm in comms_adj])
    if features["fuels_combined"]:
        TRAINING_COLUMNS.append("Fuel Index")
        TEST_COLUMNS.append("Fuel Index")
    if features["imports_exports"]:
        TRAINING_COLUMNS.append("Net Position Trade")
        TEST_COLUMNS.append("Net Position Trade")
    if features["hour_dummies"]:
        TRAINING_COLUMNS.extend([f"Hour_{i}" for i in range(0, HOURS)])
        TEST_COLUMNS.extend([f"Hour_{i}" for i in range(0, HOURS)])
    if features["month_dummies"]:
        TRAINING_COLUMNS.extend([f"Month_{i}" for i in range(1, 13)])
        TEST_COLUMNS.extend([f"Month_{i}" for i in range(1, 13)])
    if features["weekday_dummies"]:
        TRAINING_COLUMNS.extend([f"Weekday_{i}" for i in range(0, 7)])
        TEST_COLUMNS.extend([f"Weekday_{i}" for i in range(0, 7)])
    if features["workday_dummy"]:
        TRAINING_COLUMNS.append("Workday")
        TEST_COLUMNS.append("Workday")
    if features["seasonality_continuous_dummy"]:
        TRAINING_COLUMNS.extend(["Seasonality Sin", "Seasonality Cos"])
        TEST_COLUMNS.extend(["Seasonality Sin", "Seasonality Cos"])

    # data_import
    result = _validate_and_prepare(df, forecast_day, gran, training_period, COLUMNS_NEEDED)
    if result is None:
        return
    data_df, gran_adj = result

    # feature_building
    data_df = _apply_lr_features(data_df, features, gran)
    data_df, times, scaffold, fdfi, tpsi = _hourly_setup(
        data_df, forecast_day, training_period, gran, "LSTR Hourly Forecast"
    )

    META_COLS = ["Price", "Timestamp Berlin", "Date", "Time"]
    FEATURE_TRAIN_COLS = [c for c in TRAINING_COLUMNS if c not in META_COLS]
    FEATURE_TEST_COLS = [c for c in TEST_COLUMNS if c not in META_COLS]

    if z_name not in FEATURE_TRAIN_COLS:
        print(f"Error: z_name='{z_name}' not in selected features. Enable the corresponding flag.")
        return None
    z_idx = FEATURE_TRAIN_COLS.index(z_name)
    z_test_col = FEATURE_TEST_COLS[z_idx]
    col_rename = dict(zip(FEATURE_TEST_COLS, FEATURE_TRAIN_COLS))

    if z2_name is not None:
        if z2_name not in FEATURE_TRAIN_COLS:
            print(f"Error: z2_name='{z2_name}' not in selected features. Enable the corresponding flag.")
            return None
        z2_idx = FEATURE_TRAIN_COLS.index(z2_name)
        z2_test_col = FEATURE_TEST_COLS[z2_idx]

    _z2_as_feature = z_as_feature if z2_as_feature is None else z2_as_feature
    z2_norm_start_date = None
    if z2_name is not None and z2_norm_window_days is not None:
        z2_norm_start_date = (forecast_day - dt.timedelta(days=z2_norm_window_days)).date()
    lstr_results = []

    for time_idx, time in enumerate(times):
        data_time_df = data_df[data_df["Time"] == time].copy()

        train_df = data_time_df.loc[tpsi:fdfi, TRAINING_COLUMNS].copy()
        train_y = train_df["Price"].values
        if transform_target and transform_target_method == "asinh":
            train_y = np.arcsinh(c_asinh * train_y)
        train_X_df = train_df.drop(columns=META_COLS)
        z_train = train_X_df[z_name].values
        z_train_mean, z_train_std = z_train.mean(), z_train.std()
        if z2_name is not None:
            z2_train = train_X_df[z2_name].values
            if z2_norm_start_date is not None:
                z2_norm_values = z2_train[(train_df["Date"] >= z2_norm_start_date).values]
            else:
                z2_norm_values = z2_train
            z2_train_mean, z2_train_std = z2_norm_values.mean(), z2_norm_values.std()
        drop_z_cols = []
        if not z_as_feature:
            drop_z_cols.append(z_name)
        if z2_name is not None and z2_name != z_name and not _z2_as_feature:
            drop_z_cols.append(z2_name)
        if drop_z_cols:
            train_X_df = train_X_df.drop(columns=drop_z_cols)
        if add_intercept:
            train_X_df.insert(0, "Intercept", 1.0)

        test_df = data_time_df.loc[fdfi:fdfi + gran_adj - 1, TEST_COLUMNS].copy()
        test_Y = test_df["Price"]
        z_test_raw = test_df[z_test_col].values
        test_drop = list(META_COLS)
        if not z_as_feature:
            test_drop.append(z_test_col)
        if z2_name is not None and z2_name != z_name and not _z2_as_feature:
            test_drop.append(z2_test_col)
        # Rename forecast columns to training names so betas apply in the correct column order
        test_X_np = test_df.drop(columns=test_drop).rename(columns=col_rename).values
        if add_intercept:
            test_X_np = np.hstack([np.ones((test_X_np.shape[0], 1)), test_X_np])

        # feature normalization
        X_norm_means = None
        X_norm_stds = None
        if normalize_features:
            feat_cols = list(train_X_df.columns)
            norm_mask = np.array([not _is_dummy_col(c) for c in feat_cols])
            X_arr = train_X_df.values.astype(float)
            col_means = X_arr.mean(axis=0)
            col_stds = X_arr.std(axis=0)
            X_norm_means = np.where(norm_mask, col_means, 0.0)
            X_norm_stds = np.where(norm_mask & (col_stds > 1e-10), col_stds, 1.0)
            train_X_df = pd.DataFrame(
                (X_arr - X_norm_means) / X_norm_stds,
                columns=feat_cols, index=train_X_df.index
            )
            test_X_np = (test_X_np - X_norm_means) / X_norm_stds

        # fitting
        z_train_normalized = (z_train - z_train_mean) / z_train_std
        if z2_name is not None:
            z2_normalized = (z2_train - z2_train_mean) / z2_train_std

        _exact = exact_params[time_idx] if (exact_params is not None and time_idx < len(exact_params)) else None
        if _exact is not None and _exact.get("success", False):
            lstr_result = _exact
        elif z2_name is not None:
            warm_start = prev_params[time_idx] if (prev_params is not None and time_idx < len(prev_params)) else None
            lstr_result = _fit_lstr2_concentrated(
                train_X_df.values, train_y, z_train_normalized, z2_normalized,
                warm_start=warm_start, multiply_g1=multiply_g1_into_g2,
                g2_gate=g2_gate, softmin_k=softmin_k
            )
        elif z_as_feature:
            warm_start = prev_params[time_idx] if (prev_params is not None and time_idx < len(prev_params)) else None
            if use_robust:
                lstr_result = robust_lstr_fit(train_X_df, train_y, z_name=z_name,
                                              warm_start=warm_start)
            else:
                lstr_result = fit_lstr(train_X_df, train_y, z_name=z_name)
        else:
            warm_start = prev_params[time_idx] if (prev_params is not None and time_idx < len(prev_params)) else None
            lstr_result = _fit_lstr_external_z(
                train_X_df, train_y, z_train_normalized,
                use_robust=use_robust, warm_start=warm_start
            )

        if normalize_features and X_norm_means is not None and lstr_result.get("success", False):
            lstr_result = {**lstr_result, "X_norm_means": X_norm_means, "X_norm_stds": X_norm_stds}

        # predicting
        i = scaffold[scaffold["Time"] == time].index
        scaffold.loc[i, "Real Price"] = test_Y.values[0]

        if lstr_result.get("success", False):
            if z2_name is not None:
                z2_test_raw = test_df[z2_test_col].values
                y_pred = _predict_lstr2_params(
                    test_X_np, z_test_raw, z2_test_raw,
                    lstr_result["beta0"], lstr_result["beta1"], lstr_result["beta2"],
                    lstr_result["gamma1"], lstr_result["c1"],
                    lstr_result["gamma2"], lstr_result["c2"],
                    z_train_mean, z_train_std, z2_train_mean, z2_train_std,
                    multiply_g1=multiply_g1_into_g2, g2_gate=g2_gate, softmin_k=softmin_k
                )
            else:
                y_pred = _predict_lstr_params(
                    test_X_np, z_test_raw,
                    lstr_result["beta0"], lstr_result["beta1"],
                    lstr_result["gamma"], lstr_result["c"],
                    z_train_mean, z_train_std
                )
            if transform_target and transform_target_method == "asinh":
                y_pred = np.sinh(y_pred) / c_asinh
            scaffold.loc[i, "LSTR Hourly Forecast"] = round(float(y_pred[0]), 2)
        else:
            ols = sm.OLS(train_y, train_X_df).fit()
            ols_pred = ols.predict(test_X_np)
            if transform_target and transform_target_method == "asinh":
                ols_pred = np.sinh(ols_pred) / c_asinh
            scaffold.loc[i, "LSTR Hourly Forecast"] = round(float(ols_pred[0]), 2)

        lstr_results.append(lstr_result)

    forecast_df = scaffold[["Timestamp Berlin", "LSTR Hourly Forecast", "Real Price"]].reset_index(drop=True)
    return forecast_df, lstr_results


###########################################################################################################
### --- SAVE / LOAD LSTR RUNS --- ###
###########################################################################################################

_REPLAY_EXCLUDE_KEYS = {"y_pred", "G"}

_NAMED_ARRAY_KEYS = (
    "beta0", "beta1", "beta2",
    "beta0_se", "beta1_se", "beta2_se",
    "beta0_tvalues", "beta1_tvalues", "beta2_tvalues",
    "beta0_pvalues", "beta1_pvalues", "beta2_pvalues",
)


class _NumpyEncoder(json.JSONEncoder):
    """JSON encoder that serializes numpy arrays as tagged dicts."""
    def default(self, obj):
        if isinstance(obj, np.ndarray):
            return {"__ndarray__": True, "data": obj.tolist()}
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        return super().default(obj)


def _numpy_decoder(d):
    if "__ndarray__" in d:
        return np.array(d["data"])
    return d


def _forecast_csv_path(path: str) -> str:
    """
    _forecast_csv_path derives the companion forecast-dataframe CSV path for a saved LSTR run.

    :param path: Path to the run's JSON file (as passed to save_lstr_run / load_lstr_run).
    :return: Path with the JSON extension replaced by "_forecast.csv".
    """
    import os
    stem, _ext = os.path.splitext(path)
    return f"{stem}_forecast.csv"


def save_lstr_run(all_params: list, config: dict, path: str, forecast_df: pd.DataFrame = None):
    """
    save_lstr_run serializes LSTR run parameters and the run configuration to a JSON file.
    Large in-sample arrays (y_pred, G) are excluded to keep file size small.

    Beta arrays (beta0, beta1, beta2) and their conditional standard errors, t-values, and
    p-values (beta{i}_se, beta{i}_tvalues, beta{i}_pvalues) are stored as named dicts
    {feature_name: value} derived from feature_names_from_dict(config). load_lstr_run
    restores them as numpy arrays. If feature names cannot be derived from config, these
    fall back to tagged array format.

    :param all_params: List of per-day results lists as returned by test_model_forecast with
                       collect_lstr_params=True. Each element is the lstr_results_list for one
                       forecast day (one result dict per time-of-day slot).
    :param config: Dict describing the run (features, z_name, etc.). Must contain keys expected
                   by feature_names_from_dict to enable named betas.
    :param path: File path to write to (creates parent directories if needed).
    :param forecast_df: Optional forecast DataFrame for this run (e.g. the first return value of
                        test_model_forecast), containing "Timestamp Berlin", "Realized Price", and
                        one "<Method> Forecast" column per method that was run. If given, it is
                        written as a companion CSV next to path (see _forecast_csv_path) so that
                        load_lstr_run can return it without recomputation. If None, no forecast
                        CSV is written (default None).
    """
    import os
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    try:
        names = feature_names_from_dict(config, config.get("gran", 24))
    except (KeyError, TypeError):
        names = None

    def _strip_and_name(slot):
        result = {k: v for k, v in slot.items() if k not in _REPLAY_EXCLUDE_KEYS}
        if names is not None:
            for key in _NAMED_ARRAY_KEYS:
                if key in result and isinstance(result[key], np.ndarray):
                    result[key] = dict(zip(names, result[key].tolist()))
        return result

    stripped = [[_strip_and_name(slot) for slot in day] for day in all_params]
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"config": config, "params": stripped}, f, cls=_NumpyEncoder)

    if forecast_df is not None:
        forecast_df.to_csv(_forecast_csv_path(path), index=False)


def load_lstr_run(path: str, df: pd.DataFrame = None, return_forecast: bool = False,
                   forecast_methods: list = None, **forecast_kwargs):
    """
    load_lstr_run loads a previously saved LSTR run from a JSON file and restores numpy arrays.

    Named beta/SE/t-value/p-value dicts ({feature_name: value}) written by the current
    save_lstr_run are converted back to numpy arrays. Old files using the tagged
    __ndarray__ format are also handled correctly.

    If return_forecast=True, also returns the run's forecast DataFrame. It is read from the
    companion CSV written by save_lstr_run (see _forecast_csv_path) if present. If that CSV is
    missing and df is given, it is reconstructed by replaying the run via test_model_forecast
    (using exact_params_lstr=all_params, so the LSTR fit itself is not recomputed, only applied)
    and then written to the companion CSV so subsequent calls no longer need to recompute it.
    This backfill relies on config containing "forecast_day" and "forecast_interval" (present in
    all runs saved by the current save_lstr_run / notebook convention). Any extra forecast_kwargs
    (e.g. features_lr, training_period_expert) are forwarded to test_model_forecast to reproduce
    the non-LSTR methods; those methods are refit from df rather than replayed from stored
    parameters, since (unlike LSTR) they are cheap and deterministic given the same inputs.

    :param path: File path written by save_lstr_run.
    :param df: Full model DataFrame, required only to backfill a missing forecast CSV (default None).
    :param return_forecast: If True, return the run's forecast DataFrame as a third value
                            (default False, for backward compatibility with existing callers that
                            unpack `config, all_params = load_lstr_run(path)`).
    :param forecast_methods: Methods to include when backfilling the forecast DataFrame (default
                             ["Naive", "Expert", "LR Hourly", "LSTR Hourly"], matching the set used
                             throughout this project's notebooks). Ignored if the forecast CSV
                             already exists or return_forecast is False.
    :param forecast_kwargs: Extra keyword arguments forwarded to test_model_forecast when
                            backfilling (e.g. features_lr=...). Ignored if the forecast CSV already
                            exists or return_forecast is False.
    :return: Tuple (config, all_params) where config is the saved configuration dict and
             all_params is the list of per-day results lists ready to pass as exact_params_lstr
             to test_model_forecast. If return_forecast=True, a third element forecast_df is
             appended (None if it could not be loaded or backfilled).
    """
    def _restore_betas(slot):
        result = dict(slot)
        for key in _NAMED_ARRAY_KEYS:
            if key in result and isinstance(result[key], dict):
                result[key] = np.array(list(result[key].values()))
        return result

    with open(path, encoding="utf-8") as f:
        data = json.load(f, object_hook=_numpy_decoder)
    all_params = [[_restore_betas(slot) for slot in day] for day in data["params"]]
    config = data["config"]

    if not return_forecast:
        return config, all_params

    import os
    forecast_path = _forecast_csv_path(path)
    if os.path.exists(forecast_path):
        forecast_df = pd.read_csv(forecast_path)
        forecast_df["Timestamp Berlin"] = pd.to_datetime(forecast_df["Timestamp Berlin"], utc=True) \
            .dt.tz_convert("Europe/Berlin")
    elif df is not None:
        from evaluation.model_evaluation import test_model_forecast
        if forecast_methods is None:
            forecast_methods = ["Naive", "Expert", "LR Hourly", "LSTR Hourly"]
        lstr_kwargs = {k: v for k, v in config.items() if k not in ("forecast_day", "forecast_interval", "gran")}
        forecast_df, _metric_df = test_model_forecast(
            df=df,
            forecast_day=pd.to_datetime(config["forecast_day"]),
            forecast_interval=config.get("forecast_interval", 1),
            forecast_methods=forecast_methods,
            gran=config.get("gran", 24),
            exact_params_lstr=all_params,
            **lstr_kwargs,
            **forecast_kwargs,
        )
        forecast_df.to_csv(forecast_path, index=False)
    else:
        forecast_df = None

    return config, all_params, forecast_df
