"""English copy for the continuously numbered manuscript, keyed to its captions and CSV tables."""

# ruff: noqa: RUF001 -- Preserve source keys, mathematical symbols and nonbreaking references.

# A separate string for a cross-reference keeps the original Word field live.
FIGURE_CAPTIONS = {
    1: (
        "Roof patterns, cell states and fire spread without clearance.",
        "Panels a and b show roofs in Pingyan and Batuan, respectively. Ordinary roofs are grey "
        "and designated heritage structures are reddish brown. Grid north is up, and each map "
        "has its own scale bar. "
        "Panel c shows how a combustible cell changes state. The initial ignition patch starts "
        "in the burning state. Panel d shows the mean area burned out at 1 h and at modelled "
        "extinction on a logarithmic scale (1 ha = 10,000 m²). "
        "Roof area includes both ordinary and designated heritage roofs. Within each village, "
        "the near and far strata are weighted by their numbers of eligible roofs. The nine wind "
        "conditions for each roof have equal weight.",
    ),
    2: (
        "Photographic evidence for assigning construction types to building cells.",
        "Batuan dwellings (a, b) show timber façades, brick gable walls and timber boarding "
        "along an alley. Pingyan dwellings (c, d) show tiled roofs, timber boarding and posts, "
        "and stone footings. "
        "Panels e and f show the tiles and timber framing of the tiered eaves of the Batuan "
        "drum tower. Panels g and h show the tiled roof, timber gallery and stone piers of the "
        "Batuan wind-and-rain bridge. "
        "The survey team took the Batuan photographs on 11 January 2025. The Pingyan reference "
        "photographs date to 4 March 2024 (c) and 5 March 2024 (d). Photographs courtesy of the "
        "survey team. Original image IDs are listed in the accompanying data.",
    ),
    3: (
        "Model domains and equivalent fuel classes in the two villages.",
        "Maps show Pingyan (a) and Batuan (b). Colours distinguish equivalent roof fuel, woody "
        "surface fuel, open-ground fuel, noncombustible ground and areas outside the model "
        "domain. "
        "The background grid has a 5 m resolution. Terrain data sources are given in the "
        "Methods and references.",
    ),
    4: (
        "Relocating a nearly equal clearance area and its effect on cumulative roof ignition "
        "over one hour.",
        "Panels a and d show the clearance sites in Batuan and Pingyan. Panels b, c, e and f "
        "show cumulative area ignited on the 0.5 m grid, including ordinary and heritage roofs. "
        "Each panel uses a scale suited to its range. The three observations come from the same "
        "trajectory. Results at both grid resolutions are given in ",
        "Table 3",
        ".",
    ),
    5: (
        "Nominal firebreak bands and actual clearance footprints for the standard layouts.",
        "Panels a–d show Pingyan and e–h show Batuan. The multipoint layouts protect four "
        "retained ordinary roofs in addition to the designated heritage structures. "
        "Lines mark the nominal outer edges. Shading shows the roofs and whole combustible "
        "ground cells actually cleared.",
    ),
    6: (
        "Certified extinction times in full-domain reference runs.",
        "One fire scenario per village was rerun for the wide multipoint and compact layouts. "
        "Each bar ends at the certified extinction time. "
        "At 1 h and at extinction, the two solver records agreed on the number of heritage "
        "structures ignited. Absolute differences in roof and ground area burned out were zero "
        "to the nearest 0.01 m².",
    ),
    7: (
        "Relocating a nearly equal clearance area changed the time to first heritage ignition "
        "in Batuan.",
        "Panels a and b show the two local fire scenarios. Circles and squares denote the 1 m "
        "and 0.5 m grids, respectively. Filled symbols mark first ignition. Open symbols at 60 "
        "min indicate that the structure had yet to ignite. "
        "Vertical dashed lines mark 20, 40 and 60 min. The two sites differed in actual area "
        "cleared by 0.12 m².",
    ),
    8: (
        "Share of scenarios with designated heritage ignition by one hour and by extinction "
        "under five standard layouts.",
        "Panels a–d show near and far sources in Pingyan, followed by near and far sources in "
        "Batuan. Open and filled circles give the shares by 60 min and by certified extinction. "
        "Connecting lines show the increase between observations. "
        "The denominators per layout are 207, 270, 180 and 270 scenarios in a–d, respectively.",
    ),
    9: (
        "Actual clearance footprints of the wide multipoint and compact layouts.",
        "Panels a and b show Pingyan and c and d show Batuan. Both maps of each village use the "
        "same extent and scale. Reddish brown marks cleared ordinary roof area, teal marks "
        "whole combustible ground cells cleared, and dark blue marks retained heritage roofs. "
        "The area above each map is the total roof and ground clearance rounded to the nearest "
        "square metre.",
    ),
    10: (
        "Heritage ignition and ordinary roof loss under the compact and wide multipoint layouts.",
        "Panel a gives the number and share of scenarios with heritage ignition by extinction. "
        "Panel b gives the mean paired difference (compact minus wide multipoint) for ordinary "
        "roof area burned out and for the sum of roof area cleared during construction and "
        "burned out in the fire. "
        "Error bars show one sampling standard error, with clustering by ignition-source roof "
        "and a finite-population correction.",
    ),
    11: (
        "Designated heritage ignition by wind condition.",
        "Panels a–d show the four village and source-distance strata. For each wind condition, "
        "the denominator is the number of ignition-source roofs tested in that stratum. "
        "Bars show the compact layout and points the wide multipoint layout. Wind directions "
        "indicate where the wind comes from relative to grid north. Wind speed is in m/s.",
    ),
    12: (
        "Heritage ignition by structure type under the compact layout.",
        "Ignition occurred in 153 of 927 drum tower observations and 207 of 1,404 wind-and-rain "
        "bridge observations. Each structure was counted separately in every fire scenario in "
        "its village, giving different denominators. "
        "The wide multipoint layout recorded zero ignitions for both types.",
    ),
    13: (
        "Two mid-budget routes and the ordinary roofs cleared by each.",
        "The top row shows Pingyan and the bottom row Batuan. The left column shows uniform "
        "narrowing, rerouting along existing noncombustible ground, and the original wide "
        "multipoint firebreak. Rectangles mark the areas enlarged on the right, where shading "
        "highlights roofs cleared by only one route. "
        "Grid north is up. The detail windows were selected from changes in the actual "
        "clearance footprint.",
    ),
    14: (
        "Construction clearance and total ordinary roof loss at extinction for the mid-budget "
        "routes.",
        "Panel a expresses clearance as a percentage of the wide multipoint area in each "
        "village (100%) and labels the actual areas. The counts above the panels give scenarios "
        "with heritage ignition by extinction at a 15 m radiation search radius, out of 927 per "
        "route. "
        "Panel b shows the mean difference in total loss for each mid-budget route minus the "
        "wide multipoint layout. A negative value means that more ordinary roof area remained "
        "after the fire. "
        "Error bars show one sampling standard error, with clustering by ignition-source roof "
        "and a finite-population correction.",
    ),
    15: (
        "One-hour outcomes when the radiation search radius increased.",
        "Each point pairs the same ignition source, wind and layout at 25 m and 15 m. The "
        "horizontal axis gives the 25 m result minus the 15 m result. "
        "Panel a shows the change in the number of heritage structures ignited. Panel b shows "
        "the change in area burned out across all roofs. The five standard layouts yielded 20 "
        "paired comparisons per village.",
    ),
}

TABLE_TITLES = {
    1: "Spatial discretization and parameters for the three equivalent fuel classes",
    2: "Material classes assigned to roof cells from field observations of construction types",
    3: "Clearance geometry and ignition at two grid resolutions for nearly equal-area sites",
    4: "Finalized compact layouts, pilot simulations and sensitivity to radiation search radius",
    5: "Heritage, roof and ground outcomes under five standard layouts by source-distance stratum",
    6: "Simulation counts, agreement with reference runs and verification of additional results",
    7: "Clearance area and scenarios with heritage ignition by extinction for eight layouts",
    8: "Ordinary roof area burned out and total loss under four multipoint or compact layouts",
    9: "Paired differences between the mid-budget routes and the wide firebreak by stratum",
    10: "Loss distributions for the compact and wide multipoint layouts in matched fire scenarios",
}

# The first line under Table 2 is a prose paragraph inside its evidence panel.
# Other paragraphs come from the original table notes and panel headings.
NOTES = {
    "等效材料参数": ("Equivalent material parameters",),
    "两村的热传播计算": (
        "Both villages use the three fuel classes listed here. The combustible parts of all "
        "building types share the equivalent roof fuel (code 5). Field-based construction "
        "classes are given in ",
        "Table 2",
        ". Ambient temperature is 300 K and the maximum time step is 1 s.",
    ),
    "两村建筑构造的田野照片例证": ("Field photographs of construction in the two villages",),
    "类型规则与证据": ("Classification rules and supporting evidence",),
    "构造判读覆盖岜团": (
        "The construction assessment covered 290 original Batuan field photographs taken on 11 "
        "January 2025, including 6 in the dwellings folder, 21 in the drum tower folder and its "
        "subfolders, and 62 in the wind-and-rain bridge folder. The survey team also provided "
        "17 reference photographs of Pingyan dwellings taken on 4–6 March 2024. A building may "
        "appear in several views. Components were recorded by their role as coverings, supports "
        "or enclosures, following the ",
        "ICOMOS Principles for the Conservation of Wooden Built Heritage",
        ".",
    ),
    "岜团普通建筑": (
        "Batuan dwellings. Street views show timber façades, tiled roofs and brick gable walls. "
        "All ordinary roofs were assigned to the mixed-construction class with timber "
        "components. Wall and load-bearing systems can vary by building.",
    ),
    "平岩普通建筑": (
        "Pingyan dwellings. The photographs show tiled roofs, timber members beneath the eaves, "
        "timber boarding and posts. Some dwellings have stone footings. Ordinary buildings were "
        "classified as tile-roofed timber dwellings. Details outside the photographs were "
        "inferred from comparable buildings in Batuan.",
    ),
    "鼓楼　": (
        "Drum towers. The model assigns tile-covered timber construction. The photographs show "
        "overlapping tiles at the eaves and timber framing that supports the roof. The tiles "
        "form the covering and the timber provides the equivalent combustible component.",
    ),
    "风雨桥　": (
        "Wind-and-rain bridges. The model assigns tile-roofed timber gallery construction. "
        "Tiles, timber beams, railings and deck boards are visible. The masonry piers were "
        "recorded separately as supports and excluded from roof fuel.",
    ),
    "基准屋面元胞的赋值结果": ("Construction classes assigned to baseline roof cells",),
    "无清理、尚未局部细化时": (
        "Before clearance or local grid refinement, the 1,283 roof labels in the two villages "
        "represented 28,716 building cells. A roof can span several 5 m grid cells, and a grid "
        "cell can contain several roof patches. Field observations determine the cell class. "
        "Simulated moisture content, fuel load and ignition temperature use the equivalent roof "
        "parameters in ",
        "Table 1",
        ".",
    ),
    "面积单位为": (
        "All areas are in m². All values are zero for the no-clearance layout. Nominal area "
        "sums the areas of the original roof labels. Actual clearance excludes areas shared "
        "with retained roofs.",
    ),
    "固定局部火情中": ("Local fire scenarios with at least one designated heritage ignition",),
    "每种布局和每个网格": (
        "Each layout and grid resolution was tested in the same two scenarios per village. Each "
        "entry counts scenarios in which at least one designated heritage structure had ignited "
        "by that observation time.",
    ),
    "先导试验中": ("Pilot scenarios with at least one designated heritage ignition",),
    "改变辐射搜索半径后": ("One-hour differences between radiation search radii",),
    "每村每套标准布局有": (
        "Each standard layout had four paired comparisons per village at 15 m and 25 m, with "
        "the same ignition source and wind. A changed count means that different numbers of "
        "designated heritage structures had ignited by 1 h. Under the wide multipoint layout, "
        "two pairs per village had reached extinction at both radii by 1 h with no heritage "
        "ignition. In the other two pairs, cells were still burning at both radii. Roof area "
        "includes ordinary and designated heritage roofs.",
    ),
    "最后两列为": (
        "The last two columns give mean area burned out by extinction. Roof area includes "
        "ordinary and designated heritage roofs. Scenarios count each fire once if any heritage "
        "structure ignited. The structure count sums all heritage structures ignited across "
        "scenarios.",
    ),
    "按合格抽样框加权": ("Village outcomes weighted by the eligible roof population",),
    "以近、远层全部合格": (
        "Weights use all eligible near- and far-source roofs, respectively 23 and 893 in "
        "Pingyan and 20 and 282 in Batuan. The nine steady wind conditions for each roof have "
        "equal weight. Pairs are source roof–wind combinations. The any-wind share counts "
        "source roofs from which at least one wind condition produced heritage ignition. Areas "
        "denote area burned out.",
    ),
    "末行已包含": (
        "The first row includes the last row. For the three layouts in the mid-budget "
        "comparison, 2,781 extinction certificates were verified. All three layouts had the "
        "same initial ignition-patch area and energy in each matched scenario. Map clearance "
        "areas were independently recounted.",
    ),
    "参考求解器与": ("Reference solver runs compared with archived results",),
    "最大差取": (
        "Each maximum is the larger absolute difference from the comparisons at 1 h and at "
        "extinction.",
    ),
    "中档实验冻结": ("Archived identifiers for the finalized mid-budget experiment",),
    "路线注册 SHA-256": ("Route registration SHA-256",),
    "逐场三方案配对表 SHA-256": ("Matched scenario table for the three layouts, SHA-256",),
    "表中带宽指": (
        "Band width is the nominal clearance width. All layouts use a 15 m radiation search "
        "radius. Ignition counts are shown over the number of scenarios tested in each distance "
        "stratum. Roof clearance is the area of the physical union actually cleared. The two "
        "mid-budget routes clear equal areas of combustible ground within each village. The "
        "ground-aligned route can locally widen or narrow the uniform band by 1.25 m and shift "
        "its boundary. Areas and differences were calculated before rounding.",
    ),
    "均差方向为": (
        "Mean differences are compact minus wide multipoint. Roof counts use the original "
        "labels of affected ordinary roofs. Ground area burned out is measured over actual "
        "combustible cover. Contact with an artificial crop boundary and contact with the "
        "original physical domain boundary were checked separately.",
    ),
    "烧尽面积按": (
        "Area burned out counts retained ordinary roofs consumed by fire. Total loss also "
        "includes ordinary roof area cleared during construction. Late ignition means that the "
        "first heritage structure in the scenario ignited after 60 min. Means and maxima were "
        "calculated at extinction across scenarios in each stratum.",
    ),
    "减、平、增": (
        "Lower, equal and higher counts classify the difference in total ordinary roof loss by "
        "scenario, with a tie tolerance of 10⁻⁵ m². Standard errors use each roof’s mean "
        "difference over the nine wind conditions. The near-source strata include all eligible "
        "roofs. At a 15 m radiation search radius, all three layouts had zero designated "
        "heritage ignitions through extinction in all four strata.",
    ),
}
