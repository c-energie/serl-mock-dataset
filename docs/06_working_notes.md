# Working notes and TODOs

These are some working notes and TODOs that have been tracked as the project progresses. They are not necessarily in any particular order, but they represent some of the tasks and ideas.

    [ ] Add the possibility to **generate typical error values** observed in SERL, such as missing data or outliers, to make the datasets more realistic and to test the robustness of analysis pipelines.
    [ ] Add the possibility to select also the start and end months for the generated data.
    [ ] Add the possibility to generate **tariff data** csv files -  `serl_tariff_data*.csv`
    [ ] Check and fix follow-up survey dictionary.
    [ ] `generate_list_of_exporters()` keys the exporter PUPRN list on `has_pv`, not `has_export_meter` — decide whether the file should track PV or export-meter households and align the name/logic.
    [ ] `profiles.gas_fraction` / `HouseholdProfile.has_gas` is generated but never read by `HHSmartMeterGenerator` (gas gating actually comes from `household_traits.gas_meter_fraction`) — either wire it in or remove the dead field.

    [x] improve generation of follow up survey, develop a csv dictionary similar to the one used for the main survey.
    [x] Move additonal csv file used for mock data generation into a dedicated folder (puprn_master, Elec_2023_list_of_exporters..)
    [x] Generate **daily energy consumption** and **processed data**.
    [x] Use CDS Api to retrieve **real weather data** for the same period as the energy consumption data, to allow for more realistic analysis and modeling.
    [x] Add check if weather data are already present in the mock data folder, and if not, retrieve them from CDS Api.
    [x] Check if weather data are converted into csv files, and if not, convert them to csv files.



