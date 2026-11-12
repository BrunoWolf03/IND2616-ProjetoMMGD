"""Modelos das fases 3 a 5: intervenção, candidatos de previsão, validação cruzada e simulação.

Usado pelos notebooks 03_intervencao, 04_comparacao e 05_cenarios_2030. Tudo trabalha
com o log da potência adicionada por mês (log_mw), como decidido na fase 2.

Variáveis de intervenção (na taxa de crescimento, ou seja, em Δlog y):
- desac_2020: degrau a partir de mar/2020 (desaceleração anterior à lei, Zivot-Andrews).
- corrida:    degrau de jan/2022 a jun/2023 (corrida para protocolar pela regra antiga).
- pos:        degrau a partir de jul/2023 (regime depois da corrida).
Em nível, um degrau na taxa de crescimento é uma rampa; é assim que elas entram no
SARIMAX com d = 1 (regressão com erros ARIMA, FPP3 cap. 10). A coluna "t" faz o papel
do drift: com d = 1 ela vira a constante da série diferenciada.

Cada candidato expõe a mesma interface:
    m = Candidato(...).fit(y)          # y: pd.Series de log_mw com freq MS
    media, dp = m.forecast(h)          # média e desvio-padrão do log nos h meses seguintes
    caminhos = m.simulate(h, choques)  # choques padronizados (h x n) -> caminhos do log (h x n)
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy import optimize, stats
from statsmodels.tsa.arima_process import arma2ma
from statsmodels.tsa.exponential_smoothing.ets import ETSModel
from statsmodels.tsa.statespace.sarimax import SARIMAX

DESAC_2020 = pd.Timestamp("2020-03-01")
CORRIDA = (pd.Timestamp("2022-01-01"), pd.Timestamp("2023-07-01"))  # [início, fim)
POS = CORRIDA[1]
NOMES_INTERV = ["desac_2020", "corrida", "pos"]


def carregar(path) -> tuple[pd.DataFrame, dict[str, pd.Series]]:
    """Série longa e dicionário UF -> log_mw com freq MS."""
    serie = pd.read_parquet(path)
    y = {uf: g.set_index("mes")["log_mw"].asfreq("MS") for uf, g in serie.groupby("uf", observed=True)}
    return serie, y


def _meses_desde(idx: pd.DatetimeIndex, d: pd.Timestamp) -> np.ndarray:
    return np.maximum(0, (idx.year - d.year) * 12 + idx.month - d.month)


def degraus(idx: pd.DatetimeIndex) -> pd.DataFrame:
    """Dummies de regime na taxa de crescimento (para Δlog y)."""
    return pd.DataFrame({
        "desac_2020": idx >= DESAC_2020,
        "corrida": (idx >= CORRIDA[0]) & (idx < CORRIDA[1]),
        "pos": idx >= POS,
    }, index=idx).astype(float)


def rampas(idx: pd.DatetimeIndex, t0: pd.Timestamp) -> pd.DataFrame:
    """Integral dos degraus, para entrar em nível num SARIMAX com d = 1. Inclui t (drift)."""
    fim_corrida = _meses_desde(idx, CORRIDA[1])
    return pd.DataFrame({
        "t": _meses_desde(idx, t0),
        "desac_2020": _meses_desde(idx, DESAC_2020),
        "corrida": _meses_desde(idx, CORRIDA[0]) - fim_corrida,
        "pos": fim_corrida,
    }, index=idx).astype(float)


def futuro(y: pd.Series, h: int) -> pd.DatetimeIndex:
    return pd.date_range(y.index[-1] + pd.offsets.MonthBegin(), periods=h, freq="MS")


def aicc(res) -> float:
    k = res.params.size
    n = res.nobs - res.loglikelihood_burn
    return res.aic + 2 * k * (k + 1) / (n - k - 1)


def _fit(model):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return model.fit(disp=False)


# --------------------------------------------------------------------------- candidatos
@dataclass
class Naive:
    """Passeio aleatório no log (§5.2): o último mês se repete."""
    nome: str = "naïve"
    drift: bool = False

    def fit(self, y):
        self.y = y
        d = y.diff().dropna()
        self.c = d.mean() if self.drift else 0.0
        self.sigma = (d - self.c).std(ddof=1)
        return self

    def forecast(self, h):
        k = np.arange(1, h + 1)
        n = len(self.y)
        var = k * self.sigma**2 * ((1 + k / (n - 1)) if self.drift else 1)
        return self.y.iloc[-1] + self.c * k, np.sqrt(var)

    def simulate(self, h, z, rng=None):
        c = self.c
        if rng is not None and self.drift:  # incerteza do drift, como no §5.5
            c = c + rng.standard_normal(z.shape[1]) * self.sigma / np.sqrt(len(self.y) - 1)
        return self.y.iloc[-1] + np.cumsum(c + self.sigma * z, axis=0)


@dataclass
class ARIMA:
    """SARIMAX no log. exog=None: ARIMA puro com drift; exog="interv": regressão com
    erros ARIMA(p,1,q) e as rampas de intervenção; exog="quebrada": tendência linear por
    partes com erros ARMA(p,0,q) (tendência determinística, FPP3 §9.9).
    interv escolhe quais intervenções entram (o contrafactual pré-lei usa só desac_2020)."""
    nome: str = "ARIMA"
    order: tuple | None = None   # None: escolhe (p, q) por AICc
    exog: str | None = None
    max_pq: int = 2
    drift: bool = True
    interv: tuple = tuple(NOMES_INTERV)
    res: object = field(default=None, repr=False)

    def _X(self, idx):
        if self.exog is None:
            return pd.DataFrame({"t": _meses_desde(idx, self.t0)}, index=idx).astype(float) if self.drift else None
        X = rampas(idx, self.t0)[["t", *self.interv]]
        if self.exog == "quebrada":
            X.insert(0, "const", 1.0)
        return X

    def _model(self, y, order):
        d = 0 if self.exog == "quebrada" else 1
        return SARIMAX(y, exog=self._X(y.index), order=(order[0], d, order[1]), trend="n")

    def fit(self, y):
        self.y, self.t0 = y, y.index[0]
        if self.order is not None:
            self.res = _fit(self._model(y, self.order))
        else:
            cands = [(p, q) for p in range(self.max_pq + 1) for q in range(self.max_pq + 1)]
            fits = [(aicc(r), o, r) for o in cands if (r := _fit(self._model(y, o))) is not None]
            _, self.order, self.res = min(fits, key=lambda f: f[0])
        return self

    def forecast(self, h):
        X = self._X(futuro(self.y, h))
        f = self.res.get_forecast(h, exog=X)
        return f.predicted_mean.to_numpy(), np.sqrt(f.var_pred_mean.to_numpy())

    def psi(self, h):
        """Pesos ψ da forma MA(∞) do log (ARMA integrado quando d = 1)."""
        ar = np.r_[1, -self.res.arparams] if self.order[0] else np.array([1.0])
        ma = np.r_[1, self.res.maparams] if self.order[1] else np.array([1.0])
        w = arma2ma(ar, ma, lags=h)
        return w if self.exog == "quebrada" else np.cumsum(w)

    def simulate(self, h, z, rng=None):
        """Caminhos = média prevista + Σ ψ_j σ z_{k-j}: exato para o modelo linear gaussiano
        e aceita choques externos (cópula).

        Com rng, soma a incerteza dos coeficientes de regressão (drift e intervenções),
        sorteados da normal assintótica: o caminho se desloca por (X_{T+k} - X_T)Δβ com
        d = 1 e por X_{T+k}Δβ com d = 0. Num horizonte de 4–5 anos é essa incerteza (a da
        inclinação do último regime) que domina; sem ela o leque sai estreito demais.
        """
        mu, _ = self.forecast(h)
        sig = np.sqrt(self.res.params["sigma2"])
        P = np.zeros((h, h))
        w = self.psi(h)
        for k in range(h):
            P[k, :k + 1] = w[k::-1]
        out = mu[:, None] + sig * (P @ z)
        Xf = self._X(futuro(self.y, h))
        if rng is not None and Xf is not None:
            cols = list(Xf.columns)
            cov = self.res.cov_params().loc[cols, cols].to_numpy()
            db = rng.multivariate_normal(np.zeros(len(cols)), cov, size=z.shape[1], method="eigh")
            base = 0.0 if self.exog == "quebrada" else self._X(self.y.index[-1:]).to_numpy()
            out += (Xf.to_numpy() - base) @ db.T
        return out

    @property
    def resid(self):
        return self.res.resid.iloc[1:] if self.exog != "quebrada" else self.res.resid


@dataclass
class ETS:
    """ETS no log (cap. 8): erro e tendência aditivos, amortecida por padrão."""
    nome: str = "ETS(A,Ad,N)"
    damped: bool = True
    trend: str | None = "add"

    def fit(self, y):
        self.y = y
        m = ETSModel(y, error="add", trend=self.trend, damped_trend=self.damped and self.trend is not None)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.res = m.fit(disp=False)
        return self

    def forecast(self, h):
        p = self.res.get_prediction(start=len(self.y), end=len(self.y) + h - 1)
        sf = p.summary_frame(alpha=0.3173)  # ±1 dp
        return sf["mean"].to_numpy(), ((sf["pi_upper"] - sf["pi_lower"]) / 2).to_numpy()

    def simulate(self, h, z, rng=None):
        # recursão do ETS(A,Ad,N) com erro aditivo (FPP3 §8.7), com choques externos
        # para a simulação conjunta: y = l + φb + e; l' = l + φb + αe; b' = φb + βe
        sig = np.sqrt(self.res.mse)
        a = self.res.smoothing_level
        b_ = self.res.smoothing_trend if self.trend else 0.0
        phi = self.res.damping_trend if (self.trend and self.damped) else 1.0
        l = np.full(z.shape[1], self.res.states["level"].iloc[-1])
        b = np.full(z.shape[1], self.res.states["trend"].iloc[-1] if self.trend else 0.0)
        out = np.empty_like(z)
        for i in range(h):
            e = sig * z[i]
            out[i] = l + phi * b + e
            l, b = l + phi * b + a * e, phi * b + b_ * e
        return out


def _bass_F(t, p, q):
    e = np.exp(-(p + q) * t)
    return (1 - e) / (1 + (q / p) * e)


@dataclass
class Bass:
    """Bass (1969) na potência acumulada: N(t) = m F(t + τ), F da difusão de Bass.
    Ajustado por mínimos quadrados no log da potência adicionada no mês (erro
    multiplicativo, como os demais modelos). Incerteza: erro AR(1) no log."""
    nome: str = "Bass"
    m_max: float = 1e6

    def fit(self, y):
        self.y = y
        t = np.arange(len(y), dtype=float)
        obs = y.to_numpy()

        def log_add(theta, t):
            lm, lp, lq, tau = theta
            m, p, q = np.exp(lm), np.exp(lp), np.exp(lq)
            dF = _bass_F(t + tau + 1, p, q) - _bass_F(t + tau, p, q)
            return lm + np.log(np.maximum(dF, 1e-300))

        best = None
        for m0 in [2e4, 5e4, 1.5e5]:
            for q0 in [0.02, 0.05, 0.1]:
                x0 = [np.log(m0), np.log(1e-4), np.log(q0), 0.0]
                r = optimize.least_squares(lambda th: log_add(th, t) - obs, x0,
                                           bounds=([np.log(1e2), np.log(1e-7), np.log(1e-3), -60],
                                                   [np.log(self.m_max), np.log(0.05), np.log(0.5), 120]))
                if best is None or r.cost < best.cost:
                    best = r
        self.theta, self._f = best.x, log_add
        e = obs - log_add(self.theta, t)
        self.phi = np.corrcoef(e[:-1], e[1:])[0, 1]
        self.sigma = np.std(e[1:] - self.phi * e[:-1], ddof=4)
        self.e_last = e[-1]
        self.m, self.p, self.q = np.exp(self.theta[:3])
        return self

    def _media(self, h):
        t = np.arange(len(self.y), len(self.y) + h, dtype=float)
        return self._f(self.theta, t)

    def forecast(self, h):
        k = np.arange(1, h + 1)
        mu = self._media(h) + self.phi**k * self.e_last
        var = self.sigma**2 * np.cumsum(self.phi ** (2 * (k - 1)))
        return mu, np.sqrt(var)

    def simulate(self, h, z, rng=None):
        e = np.empty_like(z)
        prev = np.full(z.shape[1], self.e_last)
        for i in range(h):
            prev = self.phi * prev + self.sigma * z[i]
            e[i] = prev
        return self._media(h)[:, None] + e


def candidatos() -> dict[str, callable]:
    """Fábricas dos modelos comparados na fase 4."""
    return {
        "naïve": lambda: Naive(),
        "drift": lambda: Naive(nome="drift", drift=True),
        "ETS(A,Ad,N)": lambda: ETS(),
        "ARIMA": lambda: ARIMA(nome="ARIMA"),
        "intervenção (d=1)": lambda: ARIMA(nome="intervenção (d=1)", exog="interv"),
        "tendência quebrada (d=0)": lambda: ARIMA(nome="tendência quebrada (d=0)", exog="quebrada"),
        "Bass": lambda: Bass(),
        "comb. ETS + Bass": lambda: Combinacao("comb. ETS + Bass", [ETS(), Bass()]),
        "comb. quebrada + Bass": lambda: Combinacao(
            "comb. quebrada + Bass", [ARIMA(exog="quebrada"), Bass()]),
    }


@dataclass
class Combinacao:
    """Média simples de previsões (FPP3 §13.4), feita caminho a caminho em MW.

    Os modelos recebem os mesmos choques, então o caminho combinado é a média de duas
    leituras do mesmo futuro, e não uma mistura de dois futuros independentes.
    """
    nome: str
    membros: list

    def fit(self, y):
        self.y = y
        self.membros = [m.fit(y) for m in self.membros]
        return self

    def simulate(self, h, z, rng=None):
        mw = np.mean([np.exp(m.simulate(h, z, rng=rng)) for m in self.membros], axis=0)
        return np.log(mw)

    def forecast(self, h, n=4000):
        s = self.simulate(h, np.random.default_rng(0).standard_normal((h, n)))
        return np.median(s, axis=1), s.std(axis=1)


# --------------------------------------------------------------------------- avaliação
def crps_amostra(amostra: np.ndarray, x: float) -> float:
    """CRPS empírico (FPP3 §5.9): E|X - x| - ½ E|X - X'|, com X, X' da distribuição prevista."""
    s = np.sort(amostra)
    n = len(s)
    e_dif = 2 * np.sum(s * (2 * np.arange(1, n + 1) - n - 1)) / n**2  # E|X - X'|
    return np.mean(np.abs(s - x)) - 0.5 * e_dif


def validacao_cruzada(y: pd.Series, fabricas: dict, origens: pd.DatetimeIndex, h: int,
                      n_sim: int = 2000, seed: int = 0) -> pd.DataFrame:
    """Origem móvel (§5.10): para cada origem, ajusta com os dados até ela e prevê h meses.

    As distribuições previstas são as simuladas (com incerteza dos parâmetros onde o
    modelo a tem). Devolve uma linha por (modelo, origem, horizonte) com o erro da
    mediana no log, o CRPS no log, o erro da média em MW e, no último horizonte, o erro e
    a cobertura do intervalo de 80% na soma dos h meses (a potência adicionada no período,
    que é o que se acumula até 2030).
    """
    rng = np.random.default_rng(seed)
    linhas = []
    for o in origens:
        treino = y[:o]
        teste = y[o + pd.offsets.MonthBegin():].iloc[:h]
        if len(teste) < h:
            continue
        z = rng.standard_normal((h, n_sim))
        soma_obs = np.exp(teste).sum()
        for nome, fab in fabricas.items():
            m = fab().fit(treino)
            sim = m.simulate(h, z, rng=np.random.default_rng(seed + 1))
            soma = np.exp(sim).sum(axis=0)
            for k in range(h):
                linhas.append({
                    "modelo": nome, "origem": o, "h": k + 1,
                    "erro_log": teste.iloc[k] - np.median(sim[k]),
                    "crps_log": crps_amostra(sim[k], teste.iloc[k]),
                    "pit": np.mean(sim[k] <= teste.iloc[k]),
                    "erro_mw": np.exp(teste.iloc[k]) - np.exp(sim[k]).mean(),
                    "erro_soma_pct": 100 * (soma_obs - soma.mean()) / soma_obs if k == h - 1 else np.nan,
                    "cobre80_soma": float(np.quantile(soma, 0.1) <= soma_obs <= np.quantile(soma, 0.9))
                                    if k == h - 1 else np.nan,
                })
    return pd.DataFrame(linhas)


def resumo_cv(cv: pd.DataFrame) -> pd.DataFrame:
    g = cv.groupby("modelo", sort=False)
    return pd.DataFrame({
        "RMSE log": g["erro_log"].apply(lambda e: np.sqrt(np.mean(e**2))),
        "CRPS log": g["crps_log"].mean(),
        "MAE MW": g["erro_mw"].apply(lambda e: e.abs().mean()),
        "viés soma 12m %": g["erro_soma_pct"].mean(),
        "|erro| soma 12m %": g["erro_soma_pct"].apply(lambda e: e.abs().mean()),
        "cobertura 80% soma": g["cobre80_soma"].mean(),
    })


def calibracao_cv(cv: pd.DataFrame) -> pd.DataFrame:
    """Calibração mês a mês pelo PIT (FPP3 §5.9): fração dos meses observados dentro do
    intervalo de 80% e de 50% previsto, e PIT médio (0,5 = sem viés; acima de 0,5 o
    observado tende a ficar acima da previsão)."""
    g = cv.groupby("modelo", sort=False)["pit"]
    return pd.DataFrame({
        "cobertura 80% mês": g.apply(lambda p: ((p >= 0.1) & (p <= 0.9)).mean()),
        "cobertura 50% mês": g.apply(lambda p: ((p >= 0.25) & (p <= 0.75)).mean()),
        "PIT médio": g.mean(),
    })


# --------------------------------------------------------------------------- simulação conjunta
def choques_copula(resid: pd.DataFrame, h: int, n: int, seed: int = 0,
                   independente: bool = False) -> dict[str, np.ndarray]:
    """Choques padronizados h x n por UF com cópula gaussiana estimada nos resíduos.

    A correlação vem dos postos (Spearman), convertida para a correlação da cópula
    gaussiana por 2 sen(πρ/6). As marginais são a distribuição empírica dos resíduos
    padronizados de cada UF (caudas reais em vez de normais), lidas pelo quantil Φ(z).
    """
    rng = np.random.default_rng(seed)
    r = resid.dropna()
    rho_s = r.corr(method="spearman").to_numpy()
    R = 2 * np.sin(np.pi * rho_s / 6)
    if independente:
        R = np.eye(len(R))
    L = np.linalg.cholesky(R)
    zg = np.einsum("ij,jhn->ihn", L, rng.standard_normal((len(R), h, n)))
    u = stats.norm.cdf(zg)
    out = {}
    for i, uf in enumerate(r.columns):
        padr = (r[uf] - r[uf].mean()) / r[uf].std(ddof=1)
        out[uf] = np.quantile(padr.to_numpy(), u[i])
    return out


def correlacao_copula(resid: pd.DataFrame) -> pd.DataFrame:
    rho_s = resid.dropna().corr(method="spearman")
    return 2 * np.sin(np.pi * rho_s / 6)


def simular_conjunto(fabrica, y: dict[str, pd.Series], z: dict[str, np.ndarray], h: int,
                     seed: int = 1) -> dict[str, np.ndarray]:
    """Ajusta o modelo em cada série e devolve os caminhos da potência adicionada em MW
    (h x n), já retransformados. Os choques z vêm de choques_copula, então os caminhos de
    estados diferentes saem com a dependência estimada nos resíduos. A média dos caminhos
    em MW é a previsão com correção de viés da retransformação (FPP3 §5.6)."""
    return {uf: np.exp(fabrica().fit(y[uf]).simulate(h, z[uf], rng=np.random.default_rng(seed)))
            for uf in z}


def reconciliar(base: dict[str, np.ndarray], total: str, partes: list[str],
                metodo: str = "mint") -> dict[str, np.ndarray]:
    """Reconcilia caminhos simulados para que partes somem o total (FPP3 cap. 11).

    Para cada horizonte, as previsões base dos nós (total e partes) são projetadas na
    coerência: y~ = S (S' W^-1 S)^-1 S' W^-1 y^, caminho a caminho. W é a covariância das
    previsões base no horizonte, estimada nos próprios caminhos: completa em "mint" (traço
    mínimo, Wickramasuriya et al., 2019), só a diagonal em "wls". Devolve os nós
    reconciliados; o total é a soma das partes reconciliadas.
    """
    nos = [total, *partes]
    S = np.vstack([np.ones(len(partes)), np.eye(len(partes))])
    h = base[total].shape[0]
    out = {uf: np.empty_like(base[uf]) for uf in partes}
    for k in range(h):
        Y = np.vstack([base[uf][k] for uf in nos])
        W = np.cov(Y)
        if metodo == "wls":
            W = np.diag(np.diag(W))
        Wi_S = np.linalg.solve(W, S)
        P = np.linalg.solve(S.T @ Wi_S, Wi_S.T)
        B = P @ Y
        for i, uf in enumerate(partes):
            out[uf][k] = B[i]
    out[total] = sum(out[uf] for uf in partes)
    return out
