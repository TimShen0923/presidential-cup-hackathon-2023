import enum
import itertools
import logging as py_logging
import pprint
import warnings
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import graphviz as gv
import numpy as np
import pandas as pd
import rich.progress as rp
import scipy.integrate
import seaborn.objects as so
import sympy as sp
import sympy.stats as sps
from absl import app, flags, logging

from app.data import (
    City,
    Vehicle,
    get_income_dataframe,
    get_tsai_sec_2_2_3_data,
    get_tsai_sec_2_3_data,
    get_tsai_sec_2_4_data,
    get_tsai_sec_2_5_data,
    get_vehicle_stock_series,
    get_vehicle_survival_rate_series,
)
from app.modules import (
    BootstrapModule,
    BusStockDensityModule,
    CarOwnershipModuleV2,
    IncomeDistributionModule,
    OperatingCarStockModule,
    ScooterOwnershipModule,
    TruckStockModule,
    VehicleSubsidyModule,
    VehicleSurvivalRateModule,
)
from app.pipelines import (
    BusStockPipeline,
    CarStockPipeline,
    OperatingCarStockPipeline,
    PerYearPipeline,
    ScooterStockPipeline,
    TruckStockPipeline,
)

flags.DEFINE_string("data_dir", "./data", "Directory for data.")
flags.DEFINE_string("result_dir", "./results", "Directory for result.")
FLAGS = flags.FLAGS


class PlotGroup(int, enum.Enum):
    PREDICTION = 0
    EXISTING = 1
    PREDICTION_OF_EXISTING = 2
    PREDICTION_CI_LOW = 3
    PREDICTION_CI_HIGH = 4


def vehicle_subsidy(
    data_dir: Path,
    result_dir: Path,
):
    logging.info("Running vehicle subsidy experiment.")

    module = VehicleSubsidyModule()

    inputs: dict[str, float] = {
        "d": 14_332,  # km/year
        "f_e": 0.15,  # kWh/km
        "f_f": 0.08,  # L/km
        "ρ_e": 0.081,  # $/kWh
        "ρ_f": 0.997,  # $/L
        "M_e": 743,  # $/year
        "M_f": 694,  # $/year
        "e": 0.14,  # kg/km
        "Q": 2_000,  # kg
        "T": 10,  # year
        "F_e": 6,  # h
        "F_f": 0.0833,  # h
        "I_e": 10,
        "C": 25_000,  # $
        "k": 1.16,
        "i_e": 0,
        "i_f": 230.75,  # $/year
        "ε": 0.1,
        "θ": 0.69,
        "β_1": 1.211e-5,
        "β_2": 0.05555,
        "β_3": 0.01831,
        "λ_1": 0.5,
        "λ_2": 0.5,
        "ΔN_v": 100_000,
    }

    output = module(**inputs)
    logging.info(f"Output values: {pprint.pformat(output)}")

    dot_str: str = sp.dotprint(
        module.E_G,
        atom=lambda x: not isinstance(x, sp.Basic),
    )
    gv.Source(dot_str).render(Path(result_dir, "total_budget.gv"), format="png")


def tsai_2023_sec_2_2_1_experiment(
    data_dir: Path,
    result_dir: Path,
    plot_age_values: Iterable[float] = np.linspace(0, 30, 100),
):
    logging.info("Running Tsai 2023 Section 2.2.1 experiment.")

    plot_objs: list[dict[str, Any]] = []

    for vehicle in rp.track([Vehicle.CAR, Vehicle.SCOOTER, Vehicle.OPERATING_CAR]):
        # data

        s: pd.Series = get_vehicle_survival_rate_series(data_dir, vehicle=vehicle)
        plot_objs.extend(
            s.reset_index()
            .assign(vehicle=vehicle.value, group=PlotGroup.EXISTING)
            .to_dict(orient="records")
        )

        # module

        module = VehicleSurvivalRateModule()
        module.fit(age=s.index.values, survival_rate=s.values, bootstrap=False)

        survival_rate_values = np.vectorize(module)(
            output=module.survival_rate, age=plot_age_values
        )

        plot_objs.extend(
            pd.DataFrame(
                {
                    "age": plot_age_values,
                    "survival_rate": survival_rate_values,
                }
            )
            .assign(vehicle=vehicle.value, group=PlotGroup.PREDICTION)
            .to_dict(orient="records")
        )

    # plotting

    df_plot: pd.DataFrame = pd.DataFrame(plot_objs)
    (
        so.Plot(
            df_plot,
            x="age",
            y="survival_rate",
            color="vehicle",
            marker="group",
            linewidth="group",
            linestyle="group",
        )
        .add(so.Line())
        .scale(
            color={
                Vehicle.CAR.value: "b",
                Vehicle.SCOOTER.value: "r",
                Vehicle.OPERATING_CAR.value: "g",
            },
            marker={
                PlotGroup.PREDICTION: None,
                PlotGroup.EXISTING: "o",
            },
            linewidth={
                PlotGroup.PREDICTION: 2,
                PlotGroup.EXISTING: 0,
            },
            linestyle={
                PlotGroup.PREDICTION: (6, 2),
                PlotGroup.EXISTING: "-",
            },
        )
        .label(x="Age (year)", y="Survival Rate")
        .layout(size=(6, 4))
        .save(Path(result_dir, "tsai-2023-sec-2-2-1.pdf"))
    )


def tsai_2023_sec_2_2_2_experiment(
    data_dir: Path,
    result_dir: Path,
    plot_years: Iterable[int] = range(2000, 2060, 10),
    plot_year_colors: Iterable[str] = [
        "b",
        "tab:orange",
        "g",
        "r",
        "tab:purple",
        "tab:brown",
    ],
    plot_income_values: Iterable[float] = np.linspace(0, 1_000_000, 100),
):
    logging.info("Running Tsai 2023 Section 2.2.2 experiment.")

    # data

    df_income: pd.DataFrame = get_income_dataframe(
        data_dir=data_dir, extrapolate_index=pd.Index(plot_years, name="year")
    )

    # module

    income_distribution_module = IncomeDistributionModule()

    plot_objs: list[Any] = []
    for year in plot_years:
        logging.info(f"Running year {year}.")

        s_income: pd.Series = df_income.loc[year]

        income_rv = income_distribution_module(
            output=income_distribution_module.income_rv,
            mean_income=s_income.adjusted_income,
            gini=s_income.gini,
        )
        income_pdf_values = np.vectorize(
            lambda income: sps.density(income_rv)(income).evalf()
        )(plot_income_values)

        plot_objs.extend(
            pd.DataFrame(
                {
                    "income": plot_income_values,
                    "income_pdf": income_pdf_values,
                }
            )
            .assign(year=year)
            .to_dict(orient="records")
        )

    # plotting

    df_plot: pd.DataFrame = pd.DataFrame(plot_objs)
    (
        so.Plot(
            df_plot,
            x="income",
            y="income_pdf",
            color="year",
        )
        .add(so.Line())
        .scale(color=dict(zip(plot_years, plot_year_colors)))
        .label(x="Disposable Income", y="Probability Density")
        .layout(size=(6, 4))
        .save(Path(result_dir, "tsai-2023-sec-2-2-2.pdf"))
    )


def tsai_2023_sec_2_2_3_experiment(
    data_dir: Path,
    result_dir: Path,
    income_bins_total: int = 100,
    income_bins_removed: int = 1,
    bootstrap_runs: int = 100,
    plot_income_values: Iterable[float] = np.linspace(0, 2_000_000, 100),
    plot_ownership_quantiles: Iterable[float] = np.arange(0, 1.001, 0.1),
):
    logging.info("Running Tsai 2023 Section 2.2.3 experiment.")

    for vehicle in [Vehicle.CAR, Vehicle.SCOOTER]:
        vehicle_str: str = vehicle.value.lower()
        vehicle_title: str = vehicle.replace("_", " ").title()
        logging.info(f"Vehicle type: {vehicle_str}")

        # data

        df_vehicle_ownership: pd.DataFrame = get_tsai_sec_2_2_3_data(
            data_dir, vehicle=vehicle, income_bins=income_bins_total
        )
        df_vehicle_ownership_to_fit: pd.DataFrame = df_vehicle_ownership.head(
            -income_bins_removed
        ).tail(-income_bins_removed)

        plot_objs: list[dict[str, Any]] = (
            df_vehicle_ownership.reset_index()
            .assign(percentage=-1, group=PlotGroup.EXISTING)
            .to_dict(orient="records")
        )

        # module

        module: CarOwnershipModuleV2 | ScooterOwnershipModule
        if vehicle == Vehicle.CAR:
            module = CarOwnershipModuleV2()
        elif vehicle == Vehicle.SCOOTER:
            module = ScooterOwnershipModule()
        else:
            raise ValueError(f"Unknown vehicle_str type: {vehicle}.")

        bootstrap_module = BootstrapModule(module=module, runs=bootstrap_runs)
        bootstrap_module.fit(
            income=df_vehicle_ownership_to_fit.adjusted_income.values,
            ownership=df_vehicle_ownership_to_fit.adjusted_vehicle_ownership.values,
        )

        # predictions

        df_predictions: list[pd.DataFrame] = []
        for income in plot_income_values:
            ownership_values = bootstrap_module(output=module.ownership, income=income)

            df_predictions.append(
                pd.DataFrame(
                    {"adjusted_vehicle_ownership": map(float, ownership_values)}
                ).assign(adjusted_income=income)
            )

        plot_objs.extend(
            pd.concat(df_predictions, ignore_index=True)
            .groupby("adjusted_income")
            .quantile(plot_ownership_quantiles)
            .rename_axis(index={None: "percentage"})
            .reset_index()
            .assign(group=PlotGroup.PREDICTION)
            .to_dict(orient="records")
        )

        # plotting

        df_plot: pd.DataFrame = pd.DataFrame(plot_objs)
        (
            so.Plot(
                df_plot,
                x="adjusted_income",
                y="adjusted_vehicle_ownership",
                color="group",
                marker="group",
                linewidth="group",
                linestyle="group",
            )
            .add(so.Line(), group="percentage")
            .scale(
                color={
                    PlotGroup.EXISTING: "b",
                    PlotGroup.PREDICTION: "gray",
                },
                marker={
                    PlotGroup.EXISTING: "o",
                    PlotGroup.PREDICTION: None,
                },
                linewidth={
                    PlotGroup.EXISTING: 0,
                    PlotGroup.PREDICTION: 2,
                },
                linestyle={
                    PlotGroup.EXISTING: "-",
                    PlotGroup.PREDICTION: (6, 2),
                },
            )
            .limit(
                x=(np.min(plot_income_values), np.max(plot_income_values)),
                y=(0, 0.8),
            )
            .label(x="Disposable Income", y=f"{vehicle_title} Ownership")
            .layout(size=(6, 4))
            .save(Path(result_dir, f"tsai-2023-sec-2-2-3-{vehicle_str}.pdf"))
        )


def tsai_2023_sec_2_3_experiment(
    data_dir: Path,
    result_dir: Path,
    bootstrap_runs: int = 100,
    plot_gdp_per_capita_values: Iterable[float] = np.linspace(600_000, 1_500_000, 100),
    plot_stock_quantiles: Iterable[float] = np.arange(0, 1.001, 0.1),
):
    logging.info("Running Tsai 2023 Section 2.3 experiment.")

    vehicle: Vehicle = Vehicle.OPERATING_CAR
    vehicle_title: str = vehicle.replace("_", " ").title()

    # data

    df_vehicle_stock: pd.DataFrame = get_tsai_sec_2_3_data(data_dir, vehicle=vehicle)
    plot_objs: list[dict[str, Any]] = (
        df_vehicle_stock.reset_index()
        .assign(percentage=-1, group=PlotGroup.EXISTING)
        .to_dict(orient="records")
    )

    # module

    module = OperatingCarStockModule()
    bootstrap_module = BootstrapModule(module=module, runs=bootstrap_runs)
    bootstrap_module.fit(
        gdp_per_capita=df_vehicle_stock.adjusted_gdp_per_capita.values,
        vehicle_stock=df_vehicle_stock.vehicle_stock.values,
    )

    df_predictions: list[pd.DataFrame] = []
    for gdp_per_capita in plot_gdp_per_capita_values:
        vehicle_stock_values = bootstrap_module(
            output=module.vehicle_stock, gdp_per_capita=gdp_per_capita
        )

        df_predictions.append(
            pd.DataFrame({"vehicle_stock": map(float, vehicle_stock_values)}).assign(
                adjusted_gdp_per_capita=gdp_per_capita
            )
        )

    plot_objs.extend(
        pd.concat(df_predictions, ignore_index=True)
        .groupby("adjusted_gdp_per_capita")
        .quantile(plot_stock_quantiles)
        .rename_axis(index={None: "percentage"})
        .reset_index()
        .assign(group=PlotGroup.PREDICTION)
        .to_dict(orient="records")
    )

    df_plot: pd.DataFrame = pd.DataFrame(plot_objs)
    (
        so.Plot(
            df_plot,
            x="adjusted_gdp_per_capita",
            y="vehicle_stock",
            color="group",
            marker="group",
            linewidth="group",
            linestyle="group",
        )
        .add(so.Line(), group="percentage")
        .scale(
            color={
                PlotGroup.EXISTING: "b",
                PlotGroup.PREDICTION: "gray",
            },
            marker={
                PlotGroup.EXISTING: "o",
                PlotGroup.PREDICTION: None,
            },
            linewidth={
                PlotGroup.EXISTING: 0,
                PlotGroup.PREDICTION: 2,
            },
            linestyle={
                PlotGroup.EXISTING: "-",
                PlotGroup.PREDICTION: (6, 2),
            },
        )
        .label(x="GDP per Capita", y=f"{vehicle_title} Stock")
        .layout(size=(6, 4))
        .save(Path(result_dir, "tsai-2023-sec-2-3.pdf"))
    )


def tsai_2023_sec_2_4_experiment(
    data_dir: Path,
    result_dir: Path,
    bootstrap_runs: int = 100,
    plot_stock_quantiles: Iterable[float] = np.arange(0, 1.001, 0.1),
):
    logging.info("Running Tsai 2023 Section 2.4 experiment.")

    vehicle: Vehicle = Vehicle.TRUCK
    vehicle_title: str = vehicle.replace("_", " ").title()

    # data

    df_vehicle_stock: pd.DataFrame = get_tsai_sec_2_4_data(data_dir, vehicle=vehicle)

    # module

    module = TruckStockModule()
    bootstrap_module = BootstrapModule(module=module, runs=bootstrap_runs)
    bootstrap_module.fit(
        log_gdp_per_capita=df_vehicle_stock.log_gdp_per_capita.values,
        population=df_vehicle_stock.population.values,
        vehicle_stock=df_vehicle_stock.vehicle_stock.values,
    )

    # predictions

    df_predictions: list[pd.DataFrame] = []
    for _, s_vehicle_stock in df_vehicle_stock.iterrows():
        vehicle_stock_values = bootstrap_module(
            output=module.vehicle_stock,
            log_gdp_per_capita=s_vehicle_stock.log_gdp_per_capita,
            population=s_vehicle_stock.population,
        )

        df_predictions.append(
            pd.DataFrame({"vehicle_stock": map(float, vehicle_stock_values)}).assign(
                log_gdp_per_capita=s_vehicle_stock.log_gdp_per_capita,
                population=s_vehicle_stock.population,
            )
        )

    # plotting

    plot_objs: list[dict[str, Any]]
    for plot_against in ["log_gdp_per_capita", "population"]:
        name: str | None = None
        xlabel: str | None = None
        if plot_against == "log_gdp_per_capita":
            name = "gdp"
            xlabel = "Logarithm of GDP per Capita"

        elif plot_against == "population":
            name = "population"
            xlabel = "Population"

        assert name is not None
        assert xlabel is not None

        plot_objs = (
            df_vehicle_stock.reset_index()
            .assign(percentage=-1, group=PlotGroup.EXISTING)
            .to_dict(orient="records")
        )
        plot_objs.extend(
            pd.concat(df_predictions, ignore_index=True)
            .groupby(plot_against)
            .quantile(plot_stock_quantiles)
            .rename_axis(index={None: "percentage"})
            .reset_index()
            .assign(group=PlotGroup.PREDICTION)
            .to_dict(orient="records")
        )

        df_plot: pd.DataFrame = pd.DataFrame(plot_objs)
        (
            so.Plot(
                df_plot,
                x=plot_against,
                y="vehicle_stock",
                color="group",
                marker="group",
                linewidth="group",
                linestyle="group",
            )
            .add(so.Line(), group="percentage")
            .scale(
                color={
                    PlotGroup.EXISTING: "b",
                    PlotGroup.PREDICTION: "gray",
                },
                marker={
                    PlotGroup.EXISTING: "o",
                    PlotGroup.PREDICTION: None,
                },
                linewidth={
                    PlotGroup.EXISTING: 0,
                    PlotGroup.PREDICTION: 2,
                },
                linestyle={
                    PlotGroup.EXISTING: "-",
                    PlotGroup.PREDICTION: (6, 2),
                },
            )
            .label(x=xlabel, y=f"{vehicle_title} Stock")
            .layout(size=(6, 4))
            .save(Path(result_dir, f"tsai-2023-sec-2-4-{name}.pdf"))
        )


def tsai_2023_sec_2_5_experiment(
    data_dir: Path,
    result_dir: Path,
    bootstrap_runs: int = 10,
    plot_population_density_values: Iterable[float] = np.linspace(0, 10_000, 25),
    plot_years: Iterable[int] = np.arange(1998, 2023),
    plot_stock_quantiles: Iterable[float] = np.arange(0, 1.001, 0.1),
):
    logging.info("Running Tsai 2023 Section 2.5 experiment.")

    vehicle: Vehicle = Vehicle.BUS
    vehicle_title: str = vehicle.replace("_", " ").title()

    # data

    df_vehicle_stock: pd.DataFrame = get_tsai_sec_2_5_data(
        data_dir=data_dir,
        vehicle=vehicle,
        cities=set(City) - set([City.TAIWAN, City.PENGHU, City.JINMA]),
    )
    min_year: int = df_vehicle_stock.year.min()

    plot_objs: list[dict[str, Any]] = (
        df_vehicle_stock.reset_index()
        .assign(percentage=-1, group=PlotGroup.EXISTING)
        .to_dict(orient="records")
    )

    # module

    module = BusStockDensityModule()
    bootstrap_module = BootstrapModule(module=module, runs=bootstrap_runs)
    bootstrap_module.fit(
        population_density=df_vehicle_stock.population_density.values,
        year=df_vehicle_stock.year.values - min_year,
        vehicle_stock_density=df_vehicle_stock.vehicle_stock_density.values,
    )

    # predictions

    df_predictions: list[pd.DataFrame] = []
    for population_density, year in rp.track(
        itertools.product(plot_population_density_values, plot_years),
    ):
        vehicle_stock_density_values = bootstrap_module(
            output=module.vehicle_stock_density,
            population_density=population_density,
            year=year - min_year,
        )

        df_predictions.append(
            pd.DataFrame(
                {"vehicle_stock_density": map(float, vehicle_stock_density_values)}
            ).assign(
                population_density=population_density,
                year=year,
            )
        )

    # plotting

    plot_objs.extend(
        pd.concat(df_predictions, ignore_index=True)
        .groupby("population_density")
        .quantile(plot_stock_quantiles)
        .rename_axis(index={None: "percentage"})
        .reset_index()
        .assign(group=PlotGroup.PREDICTION)
        .to_dict(orient="records")
    )

    df_plot: pd.DataFrame = pd.DataFrame(plot_objs)
    (
        so.Plot(
            df_plot,
            x="population_density",
            y="vehicle_stock_density",
            color="group",
            marker="group",
            linewidth="group",
            linestyle="group",
        )
        .add(so.Line(), group="percentage")
        .scale(
            color={
                PlotGroup.EXISTING: "b",
                PlotGroup.PREDICTION: "gray",
            },
            marker={
                PlotGroup.EXISTING: "o",
                PlotGroup.PREDICTION: None,
            },
            linewidth={
                PlotGroup.EXISTING: 0,
                PlotGroup.PREDICTION: 2,
            },
            linestyle={
                PlotGroup.EXISTING: "-",
                PlotGroup.PREDICTION: (6, 2),
            },
        )
        .label(x="Population Density", y=f"{vehicle_title} Stock Density")
        .layout(size=(6, 4))
        .save(Path(result_dir, f"tsai-2023-sec-2-5.pdf"))
    )


def tsai_2023_sec_3_1_experiment(
    data_dir: Path,
    result_dir: Path,
    bootstrap_fit_runs: int = 1000,
    bootstrap_predict_runs: int = 300,
    integrate_sigma: float = 64,
    predict_years: Iterable[int] = np.arange(2022, 2051),
    plot_years: Iterable[int] = np.arange(2000, 2050),
):
    logging.set_verbosity(logging.INFO)

    logging.info("Running Tsai 2023 Section 3.1 experiment.")

    for vehicle in [
        Vehicle.CAR,
        Vehicle.SCOOTER,
        Vehicle.OPERATING_CAR,
        Vehicle.TRUCK,
        Vehicle.BUS,
    ]:
        vehicle_str: str = vehicle.value.lower()
        vehicle_title: str = vehicle.replace("_", " ").title()

        s_vehicle_stock: pd.Series = get_vehicle_stock_series(data_dir, vehicle=vehicle)
        existing_years: Iterable[int] = s_vehicle_stock.index.values

        df_plots: list[pd.DataFrame] = []
        df_plots.append(s_vehicle_stock.reset_index().assign(group=PlotGroup.EXISTING))

        pipeline: PerYearPipeline
        match vehicle:
            case Vehicle.CAR:
                pipeline = CarStockPipeline(
                    data_dir=data_dir,
                    bootstrap_fit_runs=bootstrap_fit_runs,
                    bootstrap_predict_runs=bootstrap_predict_runs,
                    integrate_sigma=integrate_sigma,
                )
            case Vehicle.SCOOTER:
                pipeline = ScooterStockPipeline(
                    data_dir=data_dir,
                    bootstrap_fit_runs=bootstrap_fit_runs,
                    bootstrap_predict_runs=bootstrap_predict_runs,
                    integrate_sigma=integrate_sigma,
                )
            case Vehicle.OPERATING_CAR:
                pipeline = OperatingCarStockPipeline(
                    data_dir=data_dir,
                    bootstrap_fit_runs=bootstrap_fit_runs,
                    bootstrap_predict_runs=bootstrap_predict_runs,
                )
            case Vehicle.TRUCK:
                pipeline = TruckStockPipeline(
                    data_dir=data_dir,
                    bootstrap_fit_runs=bootstrap_fit_runs,
                    bootstrap_predict_runs=bootstrap_predict_runs,
                )
            case Vehicle.BUS:
                pipeline = BusStockPipeline(
                    data_dir=data_dir,
                    bootstrap_fit_runs=bootstrap_fit_runs,
                    bootstrap_predict_runs=bootstrap_predict_runs,
                )
            case _:
                raise ValueError(f"Invalid vehicle={vehicle}")

        years: Iterable[int] = sorted(set().union(existing_years).union(predict_years))
        for year in rp.track(years, description=vehicle):
            logging.info(f"Running year={year}")

            df_plot: pd.DataFrame = pipeline(year=year)

            percentage_groups: list[tuple[float, PlotGroup]]
            if year in existing_years:
                percentage_groups = [
                    (0.5, PlotGroup.PREDICTION_OF_EXISTING),
                ]
            else:
                percentage_groups = [
                    (0.025, PlotGroup.PREDICTION_CI_LOW),
                    (0.5, PlotGroup.PREDICTION),
                    (0.975, PlotGroup.PREDICTION_CI_HIGH),
                ]

            for percentage, group in percentage_groups:
                df_plots.append(
                    df_plot.loc[df_plot["percentage"] == percentage].assign(
                        year=year, group=group
                    )
                )

        df_plot = pd.concat(df_plots, ignore_index=True)

        # offset the predicted vehicle stock to match the existing vehicle stock

        s_vehicle_stock_predicted: pd.Series = df_plot.loc[
            (
                df_plot["year"].isin(existing_years)
                & df_plot["year"].isin(plot_years)
                & (df_plot["group"] == PlotGroup.PREDICTION_OF_EXISTING)
            )
        ].set_index("year")["vehicle_stock"]
        offset: float = pd.Series.mean(
            (
                s_vehicle_stock.loc[s_vehicle_stock_predicted.index]
                - s_vehicle_stock_predicted
            )
        )
        df_plot.loc[df_plot["group"] != PlotGroup.EXISTING, "vehicle_stock"] += offset

        # plot

        df_plot = df_plot.loc[df_plot["year"].isin(plot_years)]
        column_titles: list[tuple[str, str]] = [
            ("vehicle_stock", "Stock"),
            ("adjusted_vehicle_ownership", f"Stock Per Capita"),
        ]
        for column, title in column_titles:
            (
                so.Plot(
                    df_plot,
                    x="year",
                    y=column,
                    color="group",
                    marker="group",
                    linewidth="group",
                    linestyle="group",
                )
                .add(so.Line(), group="group")
                .scale(
                    color={
                        PlotGroup.EXISTING: "gray",
                        PlotGroup.PREDICTION: "b",
                        PlotGroup.PREDICTION_OF_EXISTING: "gray",
                        PlotGroup.PREDICTION_CI_LOW: "r",
                        PlotGroup.PREDICTION_CI_HIGH: "r",
                    },
                    marker={
                        PlotGroup.EXISTING: "o",
                        PlotGroup.PREDICTION: None,
                        PlotGroup.PREDICTION_OF_EXISTING: None,
                        PlotGroup.PREDICTION_CI_LOW: None,
                        PlotGroup.PREDICTION_CI_HIGH: None,
                    },
                    linewidth={
                        PlotGroup.EXISTING: 0,
                        PlotGroup.PREDICTION: 2,
                        PlotGroup.PREDICTION_OF_EXISTING: 2,
                        PlotGroup.PREDICTION_CI_LOW: 2,
                        PlotGroup.PREDICTION_CI_HIGH: 2,
                    },
                    linestyle={
                        PlotGroup.EXISTING: "-",  # unused
                        PlotGroup.PREDICTION: "-",
                        PlotGroup.PREDICTION_OF_EXISTING: (6, 2),
                        PlotGroup.PREDICTION_CI_LOW: (6, 2),
                        PlotGroup.PREDICTION_CI_HIGH: (6, 2),
                    },
                )
                .label(x="Year", y=f"{vehicle_title} {title}")
                .layout(size=(6, 4))
                .save(Path(result_dir, f"tsai-2023-sec-3-1-{vehicle_str}-{column}.pdf"))
            )

        # save csv

        (
            df_plot.loc[df_plot["group"] != PlotGroup.EXISTING]
            .drop(columns="group")
            .sort_values(["year", "percentage"])
            .assign(vehicle=vehicle.value)
            .to_csv(
                Path(result_dir, f"tsai-2023-sec-3-1-{vehicle_str}.csv"), index=False
            )
        )


def main(_):
    py_logging.getLogger("matplotlib.category").setLevel(py_logging.WARNING)
    warnings.filterwarnings("ignore", category=scipy.integrate.IntegrationWarning)

    logging.set_verbosity(logging.DEBUG)

    Path(FLAGS.result_dir).mkdir(parents=True, exist_ok=True)

    vehicle_subsidy(FLAGS.data_dir, FLAGS.result_dir)
    tsai_2023_sec_2_2_1_experiment(FLAGS.data_dir, FLAGS.result_dir)
    tsai_2023_sec_2_2_2_experiment(FLAGS.data_dir, FLAGS.result_dir)
    tsai_2023_sec_2_2_3_experiment(FLAGS.data_dir, FLAGS.result_dir)
    tsai_2023_sec_2_3_experiment(FLAGS.data_dir, FLAGS.result_dir)
    tsai_2023_sec_2_4_experiment(FLAGS.data_dir, FLAGS.result_dir)
    tsai_2023_sec_2_5_experiment(FLAGS.data_dir, FLAGS.result_dir)
    tsai_2023_sec_3_1_experiment(FLAGS.data_dir, FLAGS.result_dir)


if __name__ == "__main__":
    app.run(main)
