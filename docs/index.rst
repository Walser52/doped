``doped``
=========

.. image:: https://github.com/SMTG-Bham/doped/actions/workflows/test.yml/badge.svg
   :target: https://github.com/SMTG-Bham/doped/actions
.. image:: https://readthedocs.org/projects/doped/badge/?version=latest&style=flat
   :target: https://doped.readthedocs.io/en/latest/
.. image:: https://img.shields.io/pypi/v/doped
   :target: https://pypi.org/project/doped
.. image:: https://img.shields.io/conda/vn/conda-forge/doped.svg
   :target: https://anaconda.org/conda-forge/doped
.. image:: https://img.shields.io/pypi/dm/doped
   :target: https://pypi.org/project/doped
.. image:: https://joss.theoj.org/papers/10.21105/joss.06433/status.svg
   :target: https://doi.org/10.21105/joss.06433

.. raw:: html

   <img src="https://raw.githubusercontent.com/SMTG-Bham/doped/main/docs/doped_v2_logo.png" align="right" width="200" alt="Schematic of a doped (defect-containing) crystal, inspired by the biological analogy to (semiconductor) doping." title="Schematic of a doped (defect-containing) crystal, inspired by the biological analogy to (semiconductor) doping.">

``doped`` is a Python software for the generation, pre-/post-processing and analysis of defect supercell
calculations, implementing the defect simulation workflow in an efficient, reproducible, user-friendly yet
powerful and fully-customisable manner.

Tutorials showing the code functionality and usage are provided on the :ref:`Tutorials` page, and an
overview of the key advances of the package is given in the
`JOSS paper <https://doi.org/10.21105/joss.06433>`__.

.. raw:: html

    <a href="https://doi.org/10.21105/joss.06433"><img class="center" width="800" src="https://raw.githubusercontent.com/SMTG-Bham/doped/main/docs/JOSS/doped_JOSS_workflow_figure.png"></a>

Key Features
============
All features and functionality are fully-customisable:

- **Supercell Generation**: Generate an optimal supercell, maximising periodic image separation for the minimum number of atoms (computational cost).
- **Defect Generation**: Generate defect supercells and likely charge states from chemical intuition.
- **Calculation I/O**: Automatically write inputs & parse calculations (``VASP`` & other DFT/force-field codes).
- **Chemical Potentials**: Determine relevant competing phases for chemical potential limits, with automated calculation setup, parsing and analysis.
- **Defect Analysis**: Automatically parse calculation outputs to compute defect formation energies, finite-size corrections (FNV & eFNV), symmetries, degeneracies, transition levels, etc.
- **Thermodynamic Analysis**: Compute (non-)equilibrium Fermi levels, defect/carrier concentrations etc. as functions of annealing/cooling temperature, chemical potentials, full inclusion of metastable states etc.
- **Plotting**: Generate publication-quality plots of defect formation energies, chemical potential limits, defect/carrier concentrations, Fermi levels, charge corrections, etc.
- ``Python`` **Interface**: Fully-customisable and modular ``Python`` API, being plug-and-play with `ShakeNBreak`_ for `defect structure-searching <https://www.nature.com/articles/s41524-023-00973-1>`_, `easyunfold <https://smtg-bham.github.io/easyunfold/>`__ for band unfolding, `CarrierCapture.jl <https://github.com/WMD-group/CarrierCapture.jl>`__/`nonrad <https://nonrad.readthedocs.io/en/latest/>`__ for non-radiative recombination etc.
- Reproducibility, tabulation, automated compatibility/sanity checking, strain/displacement analysis, shallow defect / eigenvalue analysis, high-throughput compatibility, Wyckoff analysis...

Performance and Example Outputs
-------------------------------

.. image:: JOSS/doped_JOSS_figure.png
   :target: https://doi.org/10.21105/joss.06433

**(a)** Optimal supercell generation comparison. **(b)** Charge state estimation comparison.
Example **(c)** Kumagai-Oba (eFNV) finite-size correction plot, **(d)** defect formation energy diagram,
**(e)** chemical potential / stability region, **(f)** Fermi level vs. annealing temperature, **(g)**
defect/carrier concentrations vs. annealing temperature and **(h)** Fermi level / carrier concentration
heatmap plots from ``doped``. Automated plots of **(i,j)** single-particle eigenvalues and **(k)** site
displacements from DFT supercell calculations. See the
`JOSS paper <https://doi.org/10.21105/joss.06433>`__ for more details.

Installation
============
``doped`` can be installed via PyPI (``pip install doped``) or ``conda`` if preferred
(``conda install -c conda-forge doped; pip install pydefect``), and further instructions for setting up
``POTCAR`` files with ``pymatgen`` (needed for input file generation), if not already done, are provided
on the :ref:`Installation` page.

Citation
========

If you use ``doped`` in your research, please cite:

- S\. R. Kavanagh et al. `doped: Python toolkit for robust and repeatable charged defect supercell calculations <https://doi.org/10.21105/joss.06433>`__. *Journal of Open Source Software* 9 (96), 6433, **2024**

Literature
==========
The following literature contain useful discussions of various aspects of defect calculations:

- `Quick-Start Guide on Defect Calculations – Kim et al. <https://doi.org/10.1088/2515-7655/aba081>`__
- `Large Review on Defect Calculations – Freysoldt et al. <https://doi.org/10.1103/RevModPhys.86.253>`__
- `Guide to Understanding Formation Energy / Transition Level Diagrams – Gorai <https://3d-materials-lab.gitbook.io/3dmaterialslab-tutorials/defects/interpreting-defect-and-energy-level-diagrams>`__
- `Defect Structure Searching – Mosquera-Lois et al. <https://doi.org/10.1038/s41524-023-00973-1>`__
- `Free Energies of Defects – Mosquera-Lois et al. <https://doi.org/10.1039/D3CS00432E>`__
.. TODO: Squires perspective when ready

``ShakeNBreak``
================
As shown in the tutorials, it is highly recommended to use the `ShakeNBreak`_ approach when calculating
point defects in solids, to ensure you have identified the ground-state structures of your defects. As
detailed in the `theory paper`_, skipping this step can result in drastically incorrect formation
energies, transition levels, carrier capture (basically any property associated with defects). This
approach is followed in the :ref:`tutorials <Tutorials>`, with a more in-depth explanation and tutorial
given on
the
`ShakeNBreak`_ docs.

.. _theory paper: https://www.nature.com/articles/s41524-023-00973-1

.. image:: https://raw.githubusercontent.com/SMTG-Bham/ShakeNBreak/main/docs/SnB_Supercell_Schematic_PES_2sec_Compressed.gif

Studies using ``doped``, so far
===============================

- C\. López et al. **Chalcogen Vacancies Rule Charge Recombination in Pnictogen Chalcohalide Solar-Cell Absorbers** `arXiv <https://arxiv.org/abs/2504.18089>`__ 2025
- K\. Ogawa et al. **Defect Tolerance via External Passivation in the Photocatalyst SrTiO₃:Al** `ChemRxiv <https://doi.org/10.26434/chemrxiv-2025-j44qd>`__ 2025
- M\. S. Islam et al. **Diffusion Characteristics of Ru and Oxygen Vacancies in Ta₂O₅ for Resistive Random Access Memory Devices: A Density Functional Theory Investigation** `Advanced Electronic Materials <https://doi.org/10.1002/aelm.202500128>`__ 2025
- J\. Tu et al. **Giant switchable ferroelectric photovoltage in double-perovskite epitaxial films through chemical negative strain** `Science Advances <https://doi.org/10.1126/sciadv.ads4925>`__ 2025
- Y\. Fu & H. Lohan et al. **Factors Enabling Delocalized Charge-Carriers in Pnictogen-Based Solar Absorbers: In-depth Investigation into CuSbSe₂** `Nature Communications <https://doi.org/10.1038/s41467-024-55254-2>`__ 2025
- S\. R. Kavanagh **Identifying Split Vacancies with Foundation Models and Electrostatics** `arXiv <https://doi.org/10.48550/arXiv.2412.19330>`__ 2025
- S\. R. Kavanagh et al. **Intrinsic point defect tolerance in selenium for indoor and tandem photovoltaics** `Energy & Environmental Science <https://doi.org/10.1039/D4EE04647A>`__ 2025
- J\. Hu et al. **Enabling ionic transport in Li₃AlP₂ the roles of defects and disorder** `Journal of Materials Chemistry A <https://doi.org/10.1039/D4TA04347B>`__ 2025
- X\. Jiang et al. **Carrier lifetime killer in 4H-SiC: carrier capture path via carbon vacancies** `Journal of Materials Chemistry C <https://doi.org/10.1039/D4TC04558K>`__ 2025
- M\. R. Khan et al. **Interplay between intrinsic defects and optoelectronic properties of semi-Heusler gapped metals** `Physical Chemistry Chemical Physics <https://doi.org/10.1039/D5CP00673B>`__ 2025
- R\. Chinnappan **First-principles study of defect energetics and magnetic properties of Cr, Ru and Rh doped AlN** `Physica Scripta <https://doi.org/10.1088/1402-4896/adca71>`__ 2025
- R\. Desai et al. **Exploring the Defect Landscape and Dopability of Chalcogenide Perovskite BaZrS₃** `Journal of Physical Chemistry C <https://doi.org/10.1021/acs.jpcc.5c01597>`__ 2025
- C\. Kaewmeechai, J. Strand & A. Shluger **Structure and Migration Mechanisms of Oxygen Interstitial Defects in β-Ga₂O₃** `Physica Status Solidi B <https://onlinelibrary.wiley.com/doi/10.1002/pssb.202400652>`__ 2025
- W\. Gierlotka et al. **Thermodynamics of point defects in the AlSb phase and its influence on phase equilibrium** `Computational Materials Science <https://doi.org/10.1016/j.commatsci.2025.113934>`__ 2025
- X\. Wang et al. **Sulfur vacancies limit the open-circuit voltage of Sb₂S₃ solar cells** `ACS Energy Letters <https://doi.org/10.1021/acsenergylett.4c02722>`__ 2024
- A\. Zhang et al. **Optimizing the n-type carrier concentration of an InVO₄ photocatalyst by codoping with donors and intrinsic defects** `Physical Review Applied <https://doi.org/10.1103/PhysRevApplied.22.044047>`__ 2024
- M-L\. Wang et al. **Impact of sulfur doping on copper-substituted lead apatite** `Physical Review B <https://doi.org/10.1103/PhysRevB.110.104109>`__ 2024
- S\. Quadir et al. **Low-Temperature Synthesis of Stable CaZn₂P₂ Zintl Phosphide Thin Films as Candidate Top Absorbers** `Advanced Energy Materials <https://doi.org/10.1002/aenm.202402640>`__ 2024
- M\. Elgaml et al. **Controlling the Superconductivity of Nb₂Pd** :sub:`x` **S₅ via Reversible Li Intercalation** `Inorganic Chemistry <https://pubs.acs.org/doi/full/10.1021/acs.inorgchem.3c03524>`__ 2024
- Z\. Yuan & G. Hautier **First-principles study of defects and doping limits in CaO** `Applied Physics Letters <https://doi.org/10.1063/5.0211707>`__ 2024
- B\. E. Murdock et al. **Li-Site Defects Induce Formation of Li-Rich Impurity Phases: Implications for Charge Distribution and Performance of LiNi** :sub:`0.5-x` **M** :sub:`x` **Mn** :sub:`1.5` **O₄ Cathodes (M = Fe and Mg; x = 0.05–0.2)** `Advanced Materials <https://doi.org/10.1002/adma.202400343>`__ 2024
- A\. G. Squires et al. **Oxygen dimerization as a defect-driven process in bulk LiNiO₂** `ACS Energy Letters <https://pubs.acs.org/doi/10.1021/acsenergylett.4c01307>`__ 2024
- X\. Wang et al. **Upper efficiency limit of Sb₂Se₃ solar cells** `Joule <https://doi.org/10.1016/j.joule.2024.05.004>`__ 2024
- I\. Mosquera-Lois et al. **Machine-learning structural reconstructions for accelerated point defect calculations** `npj Computational Materials <https://doi.org/10.1038/s41524-024-01303-9>`__ 2024
- W\. Dou et al. **Band Degeneracy and Anisotropy Enhances Thermoelectric Performance from Sb₂Si₂Te₆ to Sc₂Si₂Te₆** `Journal of the American Chemical Society <https://doi.org/10.1021/jacs.4c01838>`__ 2024
- K\. Li et al. **Computational Prediction of an Antimony-based n-type Transparent Conducting Oxide: F-doped Sb₂O₅** `Chemistry of Materials <https://doi.org/10.1021/acs.chemmater.3c03257>`__ 2024
- S\. Hachmioune et al. **Exploring the Thermoelectric Potential of MgB₄: Electronic Band Structure, Transport Properties, and Defect Chemistry** `Chemistry of Materials <https://doi.org/10.1021/acs.chemmater.4c00584>`__ 2024
- Y\. Zeng et al. **Role of carbon in α-Al2O3:C crystals investigated with first-principles calculations and experiment** `Ceramics International <https://doi.org/10.1016/j.ceramint.2024.12.512>`__ 2024
- X\. Wang et al. **Four-electron negative-U vacancy defects in antimony selenide** `Physical Review B <https://journals.aps.org/prb/abstract/10.1103/PhysRevB.108.134102>`__ 2023
- Y\. Kumagai et al. **Alkali Mono-Pnictides: A New Class of Photovoltaic Materials by Element Mutation** `PRX Energy <http://dx.doi.org/10.1103/PRXEnergy.2.043002>`__ 2023
- S\. M. Liga & S. R. Kavanagh, A. Walsh, D. O. Scanlon, G. Konstantatos **Mixed-Cation Vacancy-Ordered Perovskites (Cs₂Ti** :sub:`1–x` **Sn** :sub:`x` **X₆; X = I or Br): Low-Temperature Miscibility, Additivity, and Tunable Stability** `Journal of Physical Chemistry C <https://doi.org/10.1021/acs.jpcc.3c05204>`__ 2023
- A\. T. J. Nicolson et al. **Cu₂SiSe₃ as a promising solar absorber: harnessing cation dissimilarity to avoid killer antisites** `Journal of Materials Chemistry A <https://doi.org/10.1039/D3TA02429F>`__ 2023
- Y\. W. Woo, Z. Li, Y-K. Jung, J-S. Park, A. Walsh **Inhomogeneous Defect Distribution in Mixed-Polytype Metal Halide Perovskites** `ACS Energy Letters <https://doi.org/10.1021/acsenergylett.2c02306>`__ 2023
- P\. A. Hyde et al. **Lithium Intercalation into the Excitonic Insulator Candidate Ta₂NiSe₅** `Inorganic Chemistry <https://doi.org/10.1021/acs.inorgchem.3c01510>`__ 2023
- J\. Willis, K. B. Spooner, D. O. Scanlon. **On the possibility of p-type doping in barium stannate** `Applied Physics Letters <https://doi.org/10.1063/5.0170552>`__ 2023
- J\. Cen et al. **Cation disorder dominates the defect chemistry of high-voltage LiMn** :sub:`1.5` **Ni** :sub:`0.5` **O₄ (LMNO) spinel cathodes** `Journal of Materials Chemistry A <https://doi.org/10.1039/D3TA00532A>`__ 2023
- J\. Willis & R. Claes et al. **Limits to Hole Mobility and Doping in Copper Iodide** `Chemistry of Materials <https://doi.org/10.1021/acs.chemmater.3c01628>`__ 2023
- I\. Mosquera-Lois & S. R. Kavanagh, A. Walsh, D. O. Scanlon **Identifying the ground state structures of point defects in solids** `npj Computational Materials <https://www.nature.com/articles/s41524-023-00973-1>`__ 2023
- Y\. T. Huang & S. R. Kavanagh et al. **Strong absorption and ultrafast localisation in NaBiS₂ nanocrystals with slow charge-carrier recombination** `Nature Communications <https://www.nature.com/articles/s41467-022-32669-3>`__ 2022
- S\. R. Kavanagh, D. O. Scanlon, A. Walsh, C. Freysoldt **Impact of metastable defect structures on carrier recombination in solar cells** `Faraday Discussions <https://doi.org/10.1039/D2FD00043A>`__ 2022
- Y-S\. Choi et al. **Intrinsic Defects and Their Role in the Phase Transition of Na-Ion Anode Na₂Ti₃O₇** `ACS Applied Energy Materials <https://doi.org/10.1021/acsaem.2c03466>`__ 2022
- S\. R. Kavanagh, D. O. Scanlon, A. Walsh **Rapid Recombination by Cadmium Vacancies in CdTe** `ACS Energy Letters <https://pubs.acs.org/doi/full/10.1021/acsenergylett.1c00380>`__ 2021
- C\. J. Krajewska et al. **Enhanced visible light absorption in layered Cs₃Bi₂Br₉ through mixed-valence Sn(II)/Sn(IV) doping** `Chemical Science <https://doi.org/10.1039/D1SC03775G>`__ 2021

.. Kanta
.. Oba book
.. BiOI
.. Kumagai collab paper
.. Sykes Magnetic oxide polarons
.. Kat YTOS
.. Squires (and mention benchmark test against AIRSS? See Slack message)

Acknowledgements
================

``doped`` (née ``DefectsWithTheBoys``) has benefitted from feedback from many users, in particular
members of the `Scanlon <http://davidscanlon.com/>`_ and
`Walsh <https://wmd-group.github.io/>`_ research groups who have / are using it in their work.
Direct contributors are listed in the GitHub ``Contributors`` sidebar; including Seán Kavanagh,
Alex Squires, Adair Nicolson, Irea Mosquera-Lois, Alex Ganose, Bonan Zhu, Katarina Brlec, Sabrine
Hachmioune and Savya Aggarwal.

`doped` was originally based on the excellent ``PyCDT`` (no longer maintained), but transformed and morphed
over time as more and more functionality was added. After breaking changes in ``pymatgen``, the package
was entirely refactored and rewritten, to work with the new ``pymatgen-analysis-defects`` package.

Thanks to `Chaoqun Zhang <https://github.com/Warlocat>`__ for uploading the
`YouTube tutorials <https://youtu.be/FWz7nm9qoNg>`__ with Chinese subtitles to
`Bilibili <https://www.bilibili.com/list/6073855/?sid=4603908>`__!

.. _ShakeNBreak: https://shakenbreak.readthedocs.io

.. raw:: html

    <!-- Default Statcounter code for doped docs
    https://doped.readthedocs.io -->
    <script type="text/javascript">
    var sc_project=12911549;
    var sc_invisible=1;
    var sc_security="2e6f5c70";
    </script>
    <script type="text/javascript"
    src="https://www.statcounter.com/counter/counter.js"
    async></script>
    <noscript><div class="statcounter"><a title="Web Analytics"
    href="https://statcounter.com/" target="_blank"><img
    class="statcounter"
    src="https://c.statcounter.com/12911549/0/2e6f5c70/1/"
    alt="Web Analytics"
    referrerPolicy="no-referrer-when-downgrade"></a></div></noscript>
    <!-- End of Statcounter Code -->

.. toctree::
   :hidden:
   :caption: Usage
   :maxdepth: 4

   Installation
   Python API <doped>
   Tutorials
   Tips
   Troubleshooting

.. toctree::
   :hidden:
   :caption: Information
   :maxdepth: 1

   Contributing
   Code_Compatibility
   changelog_link
   doped on GitHub <https://github.com/SMTG-Bham/doped>
