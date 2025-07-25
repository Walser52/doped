"""
Core functions and classes for defects in doped.
"""

import collections
import contextlib
import warnings
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Union

import numpy as np
from monty.serialization import dumpfn, loadfn
from pymatgen.analysis.bond_valence import BVAnalyzer
from pymatgen.analysis.defects import core, thermo, utils
from pymatgen.analysis.structure_matcher import ElementComparator, SpeciesComparator
from pymatgen.entries.computed_entries import ComputedEntry, ComputedStructureEntry
from pymatgen.io.vasp.outputs import Locpot, Outcar, Procar, Vasprun
from pymatgen.util.typing import PathLike
from scipy.constants import value as constants_value
from scipy.stats import sem

from doped import _doped_obj_properties_methods, get_mp_context
from doped.utils.efficiency import Composition, Element, PeriodicSite, Structure, StructureMatcher

if TYPE_CHECKING:
    from matplotlib.pyplot import Figure

    from doped.utils.parsing import suppress_logging

    with suppress_logging(), warnings.catch_warnings():  # type: ignore
        from pydefect.analyzer.band_edge_states import BandEdgeStates

mp = get_mp_context()  # https://github.com/python/cpython/pull/100229

_orientational_degeneracy_warning = (
    "The defect supercell has been detected to possibly have a non-scalar matrix expansion, "
    "which could be breaking the cell periodicity and possibly preventing the correct _relaxed_ "
    "point group symmetries (and thus orientational degeneracies) from being automatically "
    "determined.\n"
    "This will not affect defect formation energies / transition levels, but is important for "
    "concentrations/doping/Fermi level behaviour (see e.g. doi.org/10.1039/D2FD00043A & "
    "doi.org/10.1039/D3CS00432E).\n"
    "You can manually check (and edit) the computed defect/bulk point symmetries and "
    "corresponding orientational degeneracy factors by inspecting/editing the "
    "calculation_metadata['relaxed point symmetry']/['bulk site symmetry'] and "
    "degeneracy_factors['orientational degeneracy'] attributes."
)


@dataclass
class DefectEntry(thermo.DefectEntry):
    """
    Subclass of ``pymatgen.analysis.defects.thermo.DefectEntry`` with
    additional attributes used by ``doped``.

    Core Attributes:
        defect:
            ``doped``/``pymatgen`` defect object corresponding to the defect
            in the entry.
        charge_state:
            Charge state of the defect.
        sc_entry:
            ``pymatgen`` ``ComputedStructureEntry`` for the `defect` supercell.
        sc_defect_frac_coords:
            The fractional coordinates of the defect in the supercell.
        bulk_entry:
            ``pymatgen`` ``ComputedEntry`` for the bulk supercell reference.
            Required for calculating the defect formation energy.
        corrections:
            A dictionary of energy corrections which are summed and added to
            the defect formation energy.
        corrections_metadata:
            A dictionary that acts as a generic container for storing
            information about how the corrections were calculated. Only used
            for debugging and plotting purposes.

    Parsing Attributes:
        calculation_metadata:
            A dictionary of calculation parameters and data, used to perform
            charge corrections and compute formation energies.
        degeneracy_factors:
            A dictionary of degeneracy factors contributing to the total
            degeneracy of the defect species (such as spin and configurational
            degeneracy etc). This is an important factor in the defect
            concentration equation (see https://doi.org/10.1039/D2FD00043A and
            https://doi.org/10.1039/D3CS00432E), and so affects the output of
            the defect concentration / Fermi level functions. Spin and
            configurational (geometry) degeneracy factors are automatically
            determined by ``doped`` during parsing (for details, see the
            ``spin_degeneracy_from_vasprun()``,
            ``get_orientational_degeneracy`` and
            ``point_symmetry_from_defect_entry`` functions), but can also be
            edited in ``DefectEntry.degeneracy_factors``.
            For discussion, see:
            https://doped.readthedocs.io/en/latest/Tips.html#spin

    Generation Attributes:
        name:
            The ``doped``-generated name of the defect entry. See docstrings
            of ``DefectsGenerator`` for the doped naming algorithm.
        conventional_structure:
            Conventional cell structure of the host according to the Bilbao
            Crystallographic Server (BCS) definition, used to determine defect
            site Wyckoff labels and multiplicities.
        conv_cell_frac_coords:
            Fractional coordinates of the defect in the conventional cell.
        equiv_conv_cell_frac_coords:
            Symmetry-equivalent defect positions in fractional coordinates of
            the conventional cell.
        _BilbaoCS_conv_cell_vector_mapping:
            A vector mapping the lattice vectors of the ``spglib``-defined
            conventional cell to that of the Bilbao Crystallographic Server
            definition (for most space groups the definitions are the same).
        wyckoff:
            Wyckoff label of the defect site.
        charge_state_guessing_log:
            A log of the input & computed values used to determine charge state
            probabilities.
        defect_supercell:
            ``pymatgen`` ``Structure`` object of the defect supercell.
        defect_supercell_site:
            ``pymatgen`` ``PeriodicSite`` object of the defect in the defect
            supercell.
        equivalent_supercell_sites:
            List of ``pymatgen`` ``PeriodicSite`` objects of
            symmetry-equivalent defect sites in the defect supercell.
        bulk_supercell:
            ``pymatgen`` ``Structure`` object of the bulk (pristine,
            defect-free) supercell.
    """

    # core attributes:
    defect: "Defect"
    charge_state: int
    sc_entry: ComputedStructureEntry
    corrections: dict[str, float] = field(default_factory=dict)
    corrections_metadata: dict[str, Any] = field(default_factory=dict)
    sc_defect_frac_coords: tuple[float, float, float] | None = None
    bulk_entry: ComputedEntry | None = None
    entry_id: str | None = None

    # doped attributes:
    name: str = ""
    calculation_metadata: dict = field(default_factory=dict)
    degeneracy_factors: dict = field(default_factory=dict)
    conventional_structure: Structure | None = None
    conv_cell_frac_coords: np.ndarray | None = None
    equiv_conv_cell_frac_coords: list[np.ndarray] = field(default_factory=list)
    _BilbaoCS_conv_cell_vector_mapping: list[int] = field(default_factory=lambda: [0, 1, 2])
    wyckoff: str | None = None
    charge_state_guessing_log: dict = field(default_factory=dict)
    defect_supercell: Structure | None = None
    defect_supercell_site: PeriodicSite | None = None  # TODO: Add `from_structures` method to
    # doped DefectEntry?? (Yeah would prob be useful function to have for porting over stuff from other
    # codes etc)
    equivalent_supercell_sites: list[PeriodicSite] = field(default_factory=list)
    bulk_supercell: Structure | None = None
    _bulk_entry_energy: float | None = None
    _bulk_entry_hash: int | None = None
    _sc_entry_energy: float | None = None
    _sc_entry_hash: int | None = None

    def __post_init__(self):
        """
        Post-initialization method, using super() and self.defect.
        """
        if self.sc_entry is None and not self.entry_id:
            self.entry_id = "N/A"  # otherwise crashes unnecessarily with pymatgen defects
        super().__post_init__()
        if not self.name:
            # try get using doped functions:
            try:
                from doped.generation import get_defect_name_from_defect

                name_wout_charge = get_defect_name_from_defect(self.defect)
            except Exception:
                name_wout_charge = self.defect.name

            self.name: str = (
                f"{name_wout_charge}_{'+' if self.charge_state > 0 else ''}{self.charge_state}"
            )

    def to_json(self, filename: PathLike | None = None):
        """
        Save the ``DefectEntry`` object to a json file, which can be reloaded
        with the ``DefectEntry.from_json()`` class method.

        Note that file extensions with ".gz" will be automatically compressed
        (recommended to save space)!

        Args:
            filename (PathLike):
                Filename to save json file as. If None, the filename will
                be set as ``{DefectEntry.name}.json.gz``.
        """
        # ignore warning about oxidation states not summing to Structure charge:
        warnings.filterwarnings("ignore", message=".*unset_charge.*")

        if filename is None:
            filename = f"{self.name}.json.gz"

        dumpfn(self, filename)

    @classmethod
    def from_json(cls, filename: str):
        """
        Load a ``DefectEntry`` object from a json(.gz) file.

        Note that ``.json.gz`` files can be loaded directly.

        Args:
            filename (PathLike):
                Filename of json file to load ``DefectEntry`` from.

        Returns:
            ``DefectEntry`` object
        """
        return loadfn(filename)

    def as_dict(self) -> dict:
        """
        Return a JSON-serializable dict representation of ``DefectEntry``.

        Slightly modified from the parent function to remove any hash values,
        as these are only relevant to the current python session.
        """
        defect_entry_dict = super().as_dict()
        for key in list(defect_entry_dict.keys()):
            if "_hash" in key:
                del defect_entry_dict[key]

        return defect_entry_dict

    @classmethod
    def from_dict(cls, d: dict):
        """
        Class method to create a ``DefectEntry`` object from a dictionary.

        Defined to avoid unnecessary ``vise``/``pydefect`` INFO messages.

        Args:
            d (dict):
                Dictionary representation of the ``DefectEntry`` object.

        Returns:
            ``DefectEntry`` object
        """
        from doped.utils.parsing import suppress_logging

        with suppress_logging(), warnings.catch_warnings():  # avoid vise warning suppression:
            return super().from_dict(d)

    def _check_correction_error_and_return_output(
        self,
        correction_output,
        correction_error,
        return_correction_error=False,
        type="FNV",
        error_tolerance=0.05,
    ):
        if return_correction_error:
            if isinstance(correction_output, tuple):  # correction_output may be a tuple, so amalgamate:
                return (*correction_output, correction_error)
            return correction_output, correction_error

        if (
            correction_error > error_tolerance
        ):  # greater than 50 meV error in charge correction, warn the user
            if error_tolerance >= 0.01:  # if greater than 10 meV, round energy values to meV:
                error_val_string = f"{correction_error:.3f}"
                error_tol_string = f"{error_tolerance:.3f}"
            else:  # else give in scientific notation:
                error_val_string = f"{correction_error:.2e}"
                error_tol_string = f"{error_tolerance:.2e}"

            warnings.warn(
                f"Estimated error in the {'Freysoldt (FNV)' if type == 'FNV' else 'Kumagai (eFNV)'} "
                f"charge correction for defect {self.name} is {error_val_string} eV (i.e. which is "
                f"greater than the `error_tolerance`: {error_tol_string} eV). You may want to check "
                f"the accuracy of the correction by plotting the site potential differences (using "
                f"`defect_entry.get_{'freysoldt' if type == 'FNV' else 'kumagai'}_correction()` with "
                f"`plot=True`). Large errors are often due to unstable or shallow defect charge states ("
                f"which can't be accurately modelled with the supercell approach; see "
                f"https://doped.readthedocs.io/en/latest/Tips.html#perturbed-host-states-shallow-defects"
                f"). If this error is not acceptable, you may need to use a larger supercell for more "
                f"accurate energies."
            )

        return correction_output

    def get_freysoldt_correction(
        self,
        dielectric: float | np.ndarray | list | None = None,
        defect_locpot: PathLike | Locpot | dict | None = None,
        bulk_locpot: PathLike | Locpot | dict | None = None,
        plot: bool = False,
        filename: PathLike | None = None,
        axis=None,
        return_correction_error: bool = False,
        error_tolerance: float = 0.05,
        style_file: PathLike | None = None,
        **kwargs,
    ) -> utils.CorrectionResult:
        """
        Compute the `isotropic` Freysoldt (FNV) correction for the
        defect_entry.

        The correction is added to the ``defect_entry.corrections`` dictionary
        (to be used in following formation energy calculations). If this
        correction is used, please cite Freysoldt's original paper;
        10.1103/PhysRevLett.102.016402.

        The charge correction error is estimated by computing the average
        standard deviation of the planar-averaged potential difference in the
        sampling region, and multiplying by the defect charge. This is expected
        to be a lower bound estimate of the true charge correction error.

        The defect coordinates are taken as the relaxed defect site by default
        (``DefectEntry.defect_supercell_site``) -- which is the bulk site for vacancies,
        but this can be overridden with the ``defect_frac_coords`` keyword argument.

        Args:
            dielectric (float or int or 3x1 matrix or 3x3 matrix):
                Total dielectric constance (ionic + static contributions), in
                the same xyz Cartesian basis as the supercell calculations
                (likely but not necessarily the same as the raw output of a
                VASP dielectric calculation, if an oddly-defined primitive cell
                is used). If ``None``, then the dielectric constant is taken
                from the ``DefectEntry`` ``calculation_metadata`` if available.
                See https://doped.readthedocs.io/en/latest/GGA_workflow_tutorial.html#dielectric-constant
                for information on calculating and converging the dielectric
                constant.
            defect_locpot:
                Path to the output VASP ``LOCPOT`` file from the defect
                supercell calculation, or the corresponding ``pymatgen``
                ``Locpot`` object, or a dictionary of the planar-averaged
                potential in the form:
                ``{i: Locpot.get_average_along_axis(i) for i in [0,1,2]}``.
                If ``None``, will try to use ``defect_locpot`` from the
                ``defect_entry`` ``calculation_metadata`` if available.
            bulk_locpot:
                Path to the output VASP ``LOCPOT`` file from the bulk supercell
                calculation, or the corresponding ``pymatgen`` ``Locpot``
                object, or a dictionary of the planar-averaged potential as:
                ``{i: Locpot.get_average_along_axis(i) for i in [0,1,2]}``.
                If ``None``, will try to use ``bulk_locpot`` from the
                ``defect_entry`` ``calculation_metadata`` if available.
            plot (bool):
                Whether to plot the FNV electrostatic potential plots (for
                manually checking the behaviour of the charge correction here).
            filename (PathLike):
                Filename to save the FNV electrostatic potential plots to.
                If None, plots are not saved.
            axis (int or None):
                If int, then the FNV electrostatic potential plot along the
                specified axis (0, 1, 2 for a, b, c) will be plotted. Note that
                the output charge correction is still that for `all` axes.
                If None, then all three axes are plotted.
            return_correction_error (bool):
                If True, also returns the average standard deviation of the
                planar-averaged potential difference times the defect charge
                (which gives an estimate of the error range of the correction
                energy). Default is False.
            error_tolerance (float):
                If the estimated error in the charge correction, based on the
                variance of the potential in the sampling region, is greater
                than this value (in eV), then a warning is raised.
                (default: 0.05 eV)
            style_file (PathLike):
                Path to a ``.mplstyle`` file to use for the plot. If ``None``
                (default), uses the default doped style
                (from ``doped/utils/doped.mplstyle``).
            **kwargs:
                Additional kwargs to pass to
                ``pymatgen.analysis.defects.corrections.freysoldt.get_freysoldt_correction``
                (e.g. ``energy_cutoff``, ``mad_tol``, ``q_model``, ``step``,
                ``defect_frac_coords``).

        Returns:
            ``utils.CorrectionResults`` (summary of the corrections applied and
            metadata), and the ``matplotlib`` ``Figure`` object (or axis object
            if axis specified) if ``plot`` is True, and the estimated charge
            correction error if ``return_correction_error`` is ``True``.
        """
        from doped.corrections import get_freysoldt_correction

        if dielectric is None:
            dielectric = self.calculation_metadata.get("dielectric")
        if dielectric is None:
            raise ValueError(
                "No dielectric constant provided, either as a function argument or in "
                "defect_entry.calculation_metadata."
            )

        fnv_correction_output = get_freysoldt_correction(
            defect_entry=self,
            dielectric=dielectric,
            defect_locpot=defect_locpot,
            bulk_locpot=bulk_locpot,
            plot=plot,
            filename=filename,
            axis=axis,
            style_file=style_file,
            **kwargs,
        )
        correction = fnv_correction_output if not plot and filename is None else fnv_correction_output[0]
        self.corrections.update({"freysoldt_charge_correction": correction.correction_energy})
        self._check_if_multiple_finite_size_corrections()
        self.corrections_metadata.update({"freysoldt_charge_correction": correction.metadata.copy()})

        # check accuracy of correction:
        correction_error = np.mean(
            [
                np.sqrt(
                    correction.metadata["plot_data"][i]["pot_corr_uncertainty_md"]["stats"]["variance"]
                )
                for i in [0, 1, 2]
            ]
        ) * abs(self.charge_state)
        self.corrections_metadata.update({"freysoldt_charge_correction_error": correction_error})

        return self._check_correction_error_and_return_output(
            fnv_correction_output,
            correction_error,
            return_correction_error,
            type="FNV",
            error_tolerance=error_tolerance,
        )

    def get_kumagai_correction(
        self,
        dielectric: float | np.ndarray | list | None = None,
        defect_region_radius: float | None = None,
        excluded_indices: list[int] | None = None,
        defect_outcar: PathLike | Outcar | None = None,
        bulk_outcar: PathLike | Outcar | None = None,
        plot: bool = False,
        filename: PathLike | None = None,
        return_correction_error: bool = False,
        error_tolerance: float = 0.05,
        style_file: PathLike | None = None,
        **kwargs,
    ):
        """
        Compute the Kumagai (eFNV) finite-size charge correction for the
        defect_entry. Compatible with both isotropic/cubic and anisotropic
        systems.

        The correction is added to the ``defect_entry.corrections`` dictionary
        (to be used in following formation energy calculations).

        Typically for reasonably well-converged supercell sizes, the default
        ``defect_region_radius`` works perfectly well. However, for certain
        materials at small/intermediate supercell sizes, you may want to adjust
        this (and/or ``excluded_indices``) to ensure the best sampling of the
        plateau region away from the defect position -- ``doped`` should throw a
        warning in these cases (about the correction error being above the
        default tolerance (50 meV)). For example, with layered materials, the
        defect charge is often localised to one layer, so we may want to adjust
        ``defect_region_radius`` and/or ``excluded_indices`` to ensure that
        only sites in other layers are used for the sampling region (plateau) -
        see example on doped docs ``Tips`` page.

        The correction error is estimated by computing the standard error of
        the mean of the sampled site potential differences, multiplied by the
        defect charge. This is expected to be a lower bound estimate of the
        true charge correction error.

        If this correction is used, please cite the Kumagai & Oba (eFNV) paper:
        10.1103/PhysRevB.89.195205 and the ``pydefect`` paper: "Insights into
        oxygen vacancies from high-throughput first-principles calculations" Yu
        Kumagai, Naoki Tsunoda, Akira Takahashi, and Fumiyasu Oba Phys. Rev.
        Materials 5, 123803 (2021) -- 10.1103/PhysRevMaterials.5.123803

        The defect coordinates are taken as the relaxed defect site by default
        (``DefectEntry.defect_supercell_site``) -- which is the bulk site for vacancies,
        but this can be overridden with the ``defect_coords`` keyword argument.

        Args:
            dielectric (float or int or 3x1 matrix or 3x3 matrix):
                Total dielectric constance (ionic + static contributions), in
                the same xyz Cartesian basis as the supercell calculations
                (likely but not necessarily the same as the raw output of a
                VASP dielectric calculation, if an oddly-defined primitive cell
                is used). If ``None``, then the dielectric constant is taken
                from the ``DefectEntry`` ``calculation_metadata`` if available.
                See https://doped.readthedocs.io/en/latest/GGA_workflow_tutorial.html#dielectric-constant
                for information on calculating and converging the dielectric
                constant.
            defect_region_radius (float):
                Radius of the defect region (in Å). Sites outside the defect
                region are used for sampling the electrostatic potential far
                from the defect (to obtain the potential alignment).
                If None (default), uses the Wigner-Seitz radius of the
                supercell.
            excluded_indices (list):
                List of site indices (in the defect supercell) to exclude from
                the site potential sampling in the correction calculation/plot.
                If None (default), no sites are excluded.
            defect_outcar (PathLike or Outcar):
                Path to the output VASP OUTCAR file from the defect supercell
                calculation, or the corresponding ``pymatgen`` Outcar object.
                If ``None``, will use ``defect_supercell_site_potentials``
                from the ``defect_entry`` ``calculation_metadata`` if available.
            bulk_outcar (PathLike or Outcar):
                Path to the output VASP OUTCAR file from the bulk supercell
                calculation, or the corresponding ``pymatgen`` Outcar object.
                If None, will try use ``bulk_supercell_site_potentials`` from
                the ``defect_entry`` ``calculation_metadata`` if available.
            plot (bool):
                Whether to plot the Kumagai site potential plots (for
                manually checking the behaviour of the charge correction here).
            filename (PathLike):
                Filename to save the Kumagai site potential plots to.
                If None, plots are not saved.
            return_correction_error (bool):
                If True, also returns the standard error of the mean of the
                sampled site potential differences times the defect charge
                (which gives an estimate of the error range of the correction
                energy). Default is False.
            error_tolerance (float):
                If the estimated error in the charge correction, based on the
                variance of the potential in the sampling region, is greater
                than this value (in eV), then a warning is raised.
                (default: 0.05 eV)
            style_file (PathLike):
                Path to a ``.mplstyle`` file to use for the plot. If ``None``
                (default), uses the default doped style
                (from ``doped/utils/doped.mplstyle``).
            **kwargs:
                Additional kwargs to pass to
                ``pydefect.corrections.efnv_correction.ExtendedFnvCorrection``
                (e.g. ``charge``, ``defect_region_radius``, ``defect_coords``).

        Returns:
            ``utils.CorrectionResults`` (summary of the corrections applied and
            metadata), and the ``matplotlib`` ``Figure`` object if ``plot`` is
            ``True``, and the estimated charge correction error if
            ``return_correction_error`` is ``True``.
        """
        from doped.corrections import get_kumagai_correction

        if dielectric is None:
            dielectric = self.calculation_metadata.get("dielectric")
        if dielectric is None:
            raise ValueError(
                "No dielectric constant provided, either as a function argument or in "
                "defect_entry.calculation_metadata."
            )

        efnv_correction_output = get_kumagai_correction(
            defect_entry=self,
            dielectric=dielectric,
            defect_region_radius=defect_region_radius,
            excluded_indices=excluded_indices,
            defect_outcar=defect_outcar,
            bulk_outcar=bulk_outcar,
            plot=plot,
            filename=filename,
            style_file=style_file,
            **kwargs,
        )
        correction = efnv_correction_output if not plot and filename is None else efnv_correction_output[0]
        self.corrections.update({"kumagai_charge_correction": correction.correction_energy})
        self._check_if_multiple_finite_size_corrections()
        self.corrections_metadata.update({"kumagai_charge_correction": correction.metadata.copy()})

        # check accuracy of correction:
        efnv_corr_obj = correction.metadata["pydefect_ExtendedFnvCorrection"]
        sampled_pot_diff_array = np.array(
            [s.diff_pot for s in efnv_corr_obj.sites if s.distance > efnv_corr_obj.defect_region_radius]
        )

        # correction energy error can be estimated from standard error of the mean:
        correction_error = sem(sampled_pot_diff_array) * abs(self.charge_state)
        self.corrections_metadata.update({"kumagai_charge_correction_error": correction_error})
        return self._check_correction_error_and_return_output(
            efnv_correction_output,
            correction_error,
            return_correction_error,
            type="eFNV",
            error_tolerance=error_tolerance,
        )

    def _load_and_parse_eigenvalue_data(
        self,
        bulk_vr: PathLike | Vasprun | None = None,
        bulk_procar: PathLike | Procar | None = None,
        defect_vr: PathLike | Vasprun | None = None,
        defect_procar: PathLike | Procar | None = None,
        force_reparse: bool = False,
        clear_attributes: bool = True,
    ):
        """
        Load and parse the eigenvalue data for the defect entry, if not already
        present in the ``calculation_metadata``.

        Note that this function sets the ``eigenvalues``,
        ``projected_eigenvalues`` and ``projected_magnetisation`` attributes
        to ``None`` to reduce memory demand (as these properties are not
        required in later stages of ``doped`` analysis workflows), if
        ``clear_attributes`` is ``True`` (default).

        Args:
            bulk_vr (PathLike, Vasprun):
                Either a path to the ``VASP`` ``vasprun.xml(.gz)`` output file
                or a ``pymatgen`` ``Vasprun`` object, for the reference bulk
                supercell calculation. If ``None`` (default), tries to load
                the ``Vasprun`` object from
                ``calculation_metadata["run_metadata"]["bulk_vasprun_dict"]``,
                or, failing that, from a ``vasprun.xml(.gz)`` file at
                ``self.calculation_metadata["bulk_path"]``.
            bulk_procar (PathLike, Procar):
                Not required if projected eigenvalue data available from
                ``bulk_vr`` (i.e. ``vasprun.xml(.gz)`` file from
                ``LORBIT > 10`` calculation).
                Either a path to the ``VASP`` ``PROCAR(.gz)`` output file (with
                ``LORBIT > 10`` in the ``INCAR``) or a ``pymatgen`` ``Procar``
                object, for the reference bulk supercell calculation. If
                ``None`` (default), tries to load from a ``PROCAR(.gz)`` file
                at ``self.calculation_metadata["bulk_path"]``.
            defect_vr (PathLike, Vasprun):
                Either a path to the ``VASP`` ``vasprun.xml(.gz)`` output file
                or a ``pymatgen`` ``Vasprun`` object, for the defect supercell
                calculation. If ``None`` (default), tries to load the
                ``Vasprun`` object from
                ``self.calculation_metadata["run_metadata"]["defect_vasprun_dict"]``,
                or, failing that, from a ``vasprun.xml(.gz)`` file at
                ``self.calculation_metadata["defect_path"]``.
            defect_procar (PathLike, Procar):
                Not required if projected eigenvalue data available from
                ``defect_vr`` (i.e. ``vasprun.xml(.gz)`` file from
                ``LORBIT > 10`` calculation).
                Either a path to the ``VASP`` ``PROCAR(.gz)`` output file (with
                ``LORBIT > 10`` in the ``INCAR``) or a ``pymatgen`` ``Procar``
                object, for the defect supercell calculation. If ``None``
                (default), tries to load from a ``PROCAR(.gz)`` file at
                ``self.calculation_metadata["defect_path"]``.
            force_reparse (bool):
                Whether to force re-parsing of the eigenvalue data, even if
                already present in the ``calculation_metadata``.
            clear_attributes (bool):
                If ``True`` (default), sets the ``eigenvalues``,
                ``projected_eigenvalues`` and ``projected_magnetisation``
                attributes to ``None`` to reduce memory demand (as these
                properties are not required in later stages of ``doped``
                analysis workflows).
        """
        if self.calculation_metadata.get("eigenvalue_data") is not None and not force_reparse:
            return

        from doped.utils.eigenvalues import _parse_procar, get_band_edge_info
        from doped.utils.parsing import (
            _get_output_files_and_check_if_multiple,
            _multiple_files_warning,
            get_procar,
            get_vasprun,
            spin_degeneracy_from_vasprun,
        )

        parsed_vr_procar_dict = {}
        for vr, procar, label in [(bulk_vr, bulk_procar, "bulk"), (defect_vr, defect_procar, "defect")]:
            path = self.calculation_metadata.get(f"{label}_path")
            #which of the following conditions is true?
            print([vr is not None and not isinstance(vr, Vasprun),
                   vr is None or (isinstance(vr, Vasprun) and vr.projected_eigenvalues is None),
                   vr is None and procar is not None,
                   not isinstance(vr, Vasprun),
                   procar is not None and vr.projected_eigenvalues is None,
                   procar is None and path is not None and vr.projected_eigenvalues is None,
                   procar is None and (vr is None or vr.projected_eigenvalues is None)
                   ])

            if vr is not None and not isinstance(vr, Vasprun):  # just try loading from vasprun first
                with contextlib.suppress(Exception):
                    vr = get_vasprun(vr, parse_projected_eigen=True)  # noqa: PLW2901

            if vr is None or (isinstance(vr, Vasprun) and vr.projected_eigenvalues is None):
                (  # try load from path:
                    vr_path,
                    multiple,
                ) = _get_output_files_and_check_if_multiple("vasprun.xml", path)
                if multiple:
                    _multiple_files_warning(
                        "vasprun.xml",
                        path,
                        vr_path,
                        dir_type=label,
                    )
                with contextlib.suppress(Exception):
                    vr = get_vasprun(vr_path, parse_projected_eigen=True)  # noqa: PLW2901

            if vr is None and procar is not None:  # then try take from vasprun dict:
                with contextlib.suppress(Exception):
                    vr = Vasprun.from_dict(  # noqa: PLW2901
                        self.calculation_metadata["run_metadata"][f"{label}_vasprun_dict"]
                    )

            if not isinstance(vr, Vasprun):
                raise FileNotFoundError(
                    f"No {label} 'vasprun.xml(.gz)' file found (and successfully parsed) in path: "
                    f"{path}. Required for eigenvalue analysis!"
                )
            # try load procar data, to see if projected eigenvalues are available:
            if procar is not None and vr.projected_eigenvalues is None:
                print("PARSE_PROCAR1")
                procar = _parse_procar(procar)  # noqa: PLW2901

            if procar is None and path is not None and vr.projected_eigenvalues is None:
                print("PARSE_PROCAR2")
                # no procar, try parse from directory:
                try:
                    procar_path, multiple = _get_output_files_and_check_if_multiple("PROCAR", path)
                    if multiple:
                        _multiple_files_warning(
                            "PROCAR",
                            path,
                            procar_path,
                            dir_type=label,
                        )
                    procar = get_procar(procar_path)  # noqa: PLW2901

                    print("PARSE_PROCAR3")

                except (FileNotFoundError, IsADirectoryError):
                    procar = None  # noqa: PLW2901

            if procar is None and (vr is None or vr.projected_eigenvalues is None):
                raise FileNotFoundError(
                    f"No {label} 'PROCAR' or 'vasprun.xml(.gz)' file found (and successfully parsed) with "
                    f"projected orbitals in path: {path}. Required for eigenvalue analysis!"
                )

            parsed_vr_procar_dict[label] = (vr, procar)

        print("SKIPPED")
        bulk_vr, bulk_procar = parsed_vr_procar_dict["bulk"]
        defect_vr, defect_procar = parsed_vr_procar_dict["defect"]

        from doped.utils.efficiency import cache_species

        with cache_species(Structure):
            print("cache_species1")
            band_orb, vbm_info, cbm_info = get_band_edge_info(
                bulk_vr=bulk_vr,
                defect_vr=defect_vr,
                bulk_procar=bulk_procar,  # if None, Vasprun.projected_eigenvalues used
                defect_procar=defect_procar,  # if None, Vasprun.projected_eigenvalues used
                defect_supercell_site=self.defect_supercell_site,
            )
            print("cache_species2")

        self.calculation_metadata["eigenvalue_data"] = {
            "band_orb": band_orb,
            "vbm_info": vbm_info,
            "cbm_info": cbm_info,
        }

        if clear_attributes:
            # first check if spin degeneracy has been parsed (needs projected magnetization for SOC/NCL
            # calculations), and try parse if not:
            if "spin degeneracy" not in self.degeneracy_factors:
                with contextlib.suppress(Exception):
                    self.degeneracy_factors["spin degeneracy"] = spin_degeneracy_from_vasprun(
                        defect_vr, charge_state=self.charge_state
                    ) / spin_degeneracy_from_vasprun(bulk_vr, charge_state=0)

            # delete projected_eigenvalues attribute from defect_vr if present to expedite garbage
            # collection and thus reduce memory:
            defect_vr.projected_eigenvalues = None  # but keep for bulk_vr as this is likely being re-used
            defect_vr.projected_magnetisation = (
                None  # but keep for bulk_vr as this is likely being re-used
            )
            defect_vr.eigenvalues = None  # but keep for bulk_vr as this is likely being re-used

    def get_eigenvalue_analysis(
        self,
        plot: bool = True,
        filename: PathLike | None = None,
        bulk_vr: PathLike | Vasprun | None = None,
        bulk_procar: PathLike | Procar | None = None,
        defect_vr: PathLike | Vasprun | None = None,
        defect_procar: PathLike | Procar | None = None,
        force_reparse: bool = False,
        clear_attributes: bool = True,
        **kwargs,
    ) -> Union["BandEdgeStates", tuple["BandEdgeStates", "Figure"]]:
        r"""
        Returns information about the band edge and in-gap electronic states
        and their orbital character / localisation degree for the defect entry,
        as well as a plot of the single-particle electronic eigenvalues and
        their occupation (if ``plot=True``).

        Can be used to determine if a defect is adopting a perturbed host state
        (PHS / shallow state), see
        https://doped.readthedocs.io/en/latest/Tips.html#perturbed-host-states-shallow-defects.

        If eigenvalue data has not already been parsed for ``DefectEntry``
        (default in ``doped`` is to parse this data with ``DefectsParser``/
        ``DefectParser``, as controlled by the ``parse_projected_eigen`` flag),
        then this function will attempt to load the eigenvalue data from either
        the input ``Vasprun``/``PROCAR`` objects or files, or from the
        ``bulk/defect_path``\s in ``defect_entry.calculation_metadata``.
        If so, will initially try to load orbital projections from
        ``vasprun.xml(.gz)`` files (more accurate), or failing that from
        ``PROCAR(.gz)`` files if present.

        This function uses code from ``pydefect``, so please cite the
        ``pydefect`` paper: 10.1103/PhysRevMaterials.5.123803

        Note that this function sets the ``eigenvalues``,
        ``projected_eigenvalues`` and ``projected_magnetisation`` attributes
        to ``None`` to reduce memory demand (as these properties are not
        required in later stages of ``doped`` analysis workflows), if
        ``clear_attributes`` is ``True`` (default).

        Args:
            plot (bool):
                Whether to plot the single-particle eigenvalues.
                (Default: True)
            filename (PathLike):
                Filename to save the eigenvalue plot to (if ``plot = True``).
                If ``None`` (default), plots are not saved.
            bulk_vr (PathLike, Vasprun):
                Not required if eigenvalue data has already been parsed for
                ``DefectEntry`` (default behaviour when parsing, with data in
                ``defect_entry.calculation_metadata["eigenvalue_data"]``).
                Either a path to the ``VASP`` ``vasprun.xml(.gz)`` output file
                or a ``pymatgen`` ``Vasprun`` object, for the reference bulk
                supercell calculation. If ``None`` (default), tries to load
                the ``Vasprun`` object from
                ``calculation_metadata["run_metadata"]["bulk_vasprun_dict"]``,
                or, failing that, from a ``vasprun.xml(.gz)`` file at
                ``self.calculation_metadata["bulk_path"]``.
            bulk_procar (PathLike, Procar):
                Not required if eigenvalue data has already been parsed for
                ``DefectEntry`` (default behaviour when parsing, with data in
                ``defect_entry.calculation_metadata["eigenvalue_data"]``),
                and/or if ``bulk_vr`` was parsed with
                ``parse_projected_eigen = True``.
                Either a path to the ``VASP`` ``PROCAR(.gz)`` output file (with
                ``LORBIT > 10`` in the ``INCAR``) or a ``pymatgen`` ``Procar``
                object, for the reference bulk supercell calculation. If
                ``None`` (default), tries to load from a ``PROCAR(.gz)`` file
                at ``self.calculation_metadata["bulk_path"]``.
            defect_vr (PathLike, Vasprun):
                Not required if eigenvalue data has already been parsed for
                ``DefectEntry`` (default behaviour when parsing, with data in
                ``defect_entry.calculation_metadata["eigenvalue_data"]``).
                Either a path to the ``VASP`` ``vasprun.xml(.gz)`` output file
                or a ``pymatgen`` ``Vasprun`` object, for the defect supercell
                calculation. If ``None`` (default), tries to load the
                ``Vasprun`` object from
                ``self.calculation_metadata["run_metadata"]["defect_vasprun_dict"]``,
                or, failing that, from a ``vasprun.xml(.gz)`` file at
                ``self.calculation_metadata["defect_path"]``.
            defect_procar (PathLike, Procar):
                Not required if eigenvalue data has already been parsed for
                ``DefectEntry`` (default behaviour when parsing, with data in
                ``defect_entry.calculation_metadata["eigenvalue_data"]``),
                and/or if ``defect_vr`` was parsed with
                ``parse_projected_eigen = True``.
                Either a path to the ``VASP`` ``PROCAR(.gz)`` output file (with
                ``LORBIT > 10`` in the ``INCAR``) or a ``pymatgen`` ``Procar``
                object, for the defect supercell calculation. If ``None``
                (default), tries to load from a ``PROCAR(.gz)`` file at
                ``self.calculation_metadata["defect_path"]``.
            force_reparse (bool):
                Whether to force re-parsing of the eigenvalue data, even if
                already present in the ``calculation_metadata``.
            clear_attributes (bool):
                If ``True`` (default), sets the ``eigenvalues``,
                ``projected_eigenvalues`` and ``projected_magnetisation``
                attributes to ``None`` to reduce memory demand (as these
                properties are not required in later stages of ``doped``
                analysis workflows).
            **kwargs:
                Additional kwargs to pass to
                ``doped.utils.eigenvalues.get_eigenvalue_analysis``,
                such as ``style_file``, ``ks_levels``, ``ylims``,
                ``legend_kwargs``, ``similar_orb_criterion``,
                ``similar_energy_criterion``.

        Returns:
            ``pydefect`` ``BandEdgeStates`` object and ``matplotlib``
            ``Figure`` object (if ``plot=True``).
        """
        from doped.utils.eigenvalues import get_eigenvalue_analysis

        self._load_and_parse_eigenvalue_data(
            bulk_vr=bulk_vr,
            bulk_procar=bulk_procar,
            defect_vr=defect_vr,
            defect_procar=defect_procar,
            force_reparse=force_reparse,
            clear_attributes=clear_attributes,
        )

        if self.calculation_metadata.get("eigenvalue_data") is None:
            raise ValueError(
                "No projected eigenvalues/orbitals loaded for DefectEntry. Please parse your defects with "
                "parse_projected_eigen = True (with `DefectsParser`/`DefectParser`) or provide the "
                "necessary VASP output files for the defect and bulk supercells (see docstring)."
            )

        return get_eigenvalue_analysis(self, plot=plot, filename=filename, **kwargs)

    def _get_chempot_term(self, chemical_potentials=None) -> float:
        chemical_potentials = chemical_potentials or {}
        element_changes = {elt.symbol: change for elt, change in self.defect.element_changes.items()}
        missing_elts = [elt for elt in element_changes if elt not in chemical_potentials]
        if missing_elts:
            warnings.warn(
                f"Chemical potentials not present for elements: {missing_elts}. Assuming zero chemical "
                "potentials for these elements! (Absolute formation energies will likely be very "
                "inaccurate)"
            )

        return sum(
            chem_pot * -element_changes[el]
            for el, chem_pot in chemical_potentials.items()
            if el in element_changes
        )

    def get_ediff(self) -> float:
        """
        Get the energy difference between the defect and bulk supercell
        calculations, including finite-size corrections.

        Refactored from ``pymatgen-analysis-defects`` to be more efficient,
        reducing compute times when looping over formation energy /
        concentration functions.
        """
        if self.bulk_entry is None:
            raise RuntimeError(
                "Attempting to compute the energy difference without a defined bulk entry for the "
                "DefectEntry object!"
            )
        return self.corrected_energy - self.bulk_entry_energy

    @property
    def corrected_energy(self) -> float:
        """
        Get the energy of the defect supercell calculation, with `all`
        corrections (in ``DefectEntry.corrections``) applied.

        Refactored from ``pymatgen-analysis-defects`` to be more efficient,
        reducing compute times when looping over formation energy /
        concentration functions.
        """
        self._check_if_multiple_finite_size_corrections()
        return self.sc_entry_energy + sum(self.corrections.values())

    def _check_if_multiple_finite_size_corrections(self):
        """
        Checks that there is no double counting of finite-size charge
        corrections, in the defect_entry.corrections dict.
        """
        matching_finite_size_correction_keys = {
            key
            for key in self.corrections
            if any(x in key for x in ["FNV", "freysoldt", "Freysoldt", "Kumagai", "kumagai"])
        }
        if len(matching_finite_size_correction_keys) > 1:
            warnings.warn(
                f"It appears there are multiple finite-size charge corrections in the corrections dict "
                f"attribute for defect {self.name}. These are:"
                f"\n{matching_finite_size_correction_keys}."
                f"\nPlease ensure there is no double counting / duplication of energy corrections!"
            )

    def formation_energy(
        self,
        chempots: dict | None = None,
        limit: str | None = None,
        el_refs: dict | None = None,
        vbm: float | None = None,
        fermi_level: float = 0,
    ) -> float:
        r"""
        Compute the formation energy for the ``DefectEntry`` at a given
        chemical potential limit and fermi_level.

        Args:
            chempots (dict):
                Dictionary of chemical potentials to use for calculating the
                defect formation energy. This can have the form of:
                ``{"limits": [{'limit': [chempot_dict]}]}`` (the format
                generated by ``doped``\'s chemical potential parsing functions
                (see tutorials)) and specific limits (chemical potential
                limits) can then be chosen using ``limit``.

                Alternatively this can be a dictionary of chemical potentials
                for a single limit, in the format:
                ``{element symbol: chemical potential}``.
                If manually specifying chemical potentials this way, you can
                set the ``el_refs`` option with the DFT reference energies of
                the elemental phases, in which case it is the formal chemical
                potentials (i.e. relative to the elemental references) that
                should be given here, otherwise the absolute (DFT) chemical
                potentials should be given.

                If ``None`` (default), sets all chemical potentials to zero.
                (Default: None)
            limit (str):
                The chemical potential limit for which to
                calculate the formation energy. Can be either:

                - ``None``, default if ``chempots`` corresponds to a single
                  chemical potential limit -- otherwise will use the first
                  chemical potential limit in the ``chempots`` dict.
                - "X-rich"/"X-poor" where X is an element in the system, in
                  which case the most X-rich/poor limit will be used (e.g.
                  "Li-rich").
                - A key in the ``(self.)chempots["limits"]`` dictionary.

                The latter two options can only be used if ``chempots`` is in
                the ``doped`` format (see chemical potentials tutorial).
                (Default: None)
            el_refs (dict):
                Dictionary of elemental reference energies for the chemical
                potentials in the format:
                ``{element symbol: reference energy}`` (to determine the formal
                chemical potentials, when ``chempots`` has been manually
                specified as ``{element symbol: chemical potential}``).
                Unnecessary if ``chempots`` is provided/present in format
                generated by ``doped`` (see tutorials).
                (Default: None)
            vbm (float):
                VBM eigenvalue to use as Fermi level reference point for
                calculating formation energy. If ``None`` (default), will use
                ``"vbm"`` from the ``calculation_metadata`` dict attribute if
                present -- which corresponds to the VBM of the `bulk supercell`
                calculation by default, unless ``bulk_band_gap_vr`` is set
                during defect parsing).
            fermi_level (float):
                Value corresponding to the electron chemical potential,
                referenced to the VBM eigenvalue. Default is 0 (i.e. the VBM).

        Returns:
            Formation energy value (float)
        """
        if chempots is None:
            _no_chempots_warning("Formation energies (and concentrations)")

        dft_chempots = _get_dft_chempots(chempots, el_refs, limit)
        chempot_correction = 0 if dft_chempots is None else self._get_chempot_term(dft_chempots)
        formation_energy = self.get_ediff() + chempot_correction

        if vbm is not None:
            formation_energy += self.charge_state * (vbm + fermi_level)
        elif "vbm" in self.calculation_metadata:
            formation_energy += self.charge_state * (self.calculation_metadata["vbm"] + fermi_level)
        elif self.charge_state != 0:  # fine if charge state is zero
            warnings.warn(
                "VBM eigenvalue was not set, and is not present in DefectEntry.calculation_metadata. "
                "Formation energy will be inaccurate!"
            )

        print("RETURN FORMATION ENERGY 1: ", formation_energy)
        return formation_energy

    def _parse_and_set_symmetries_and_degeneracies(
        self,
        symprec: float | None = None,
        bulk_symprec: float | None = None,
        **kwargs,
    ):
        """
        Check if symmetry and degeneracy info is present in
        ``self.calculation_metadata``, and attempt to (re)-parse if not.

        e.g. if the ``DefectEntry`` was generated with older versions of
        ``doped``, manually, or with ``pymatgen-analysis-defects`` etc.

        Args:
            symprec (float):
                Symmetry precision to use for determining symmetry operations
                and thus point symmetries with ``spglib``, for the `relaxed`
                defect supercell. Default in ``doped`` is ``0.1`` which matches
                that used by the ``Materials Project`` and is larger than the
                ``pymatgen`` default of ``0.01`` to account for residual
                structural noise in relaxed defect supercells. If set, then
                site symmetries & degeneracies will be re-parsed/computed even
                if already present in the ``DefectEntry`` object
                ``calculation_metadata``.
                You may want to adjust for your system (e.g. if there are very
                slight octahedral distortions etc.). If
                ``fixed_symprec_and_dist_tol_factor`` is ``False`` (default),
                this value will be automatically adjusted (up to 10x, down to
                0.1x) until the identified equivalent sites from ``spglib``
                have consistent point group symmetries. Setting ``verbose`` to
                ``True`` will print information on the trialled ``symprec``
                (and ``dist_tol_factor`` values).
                (Default: None)
            bulk_symprec (float):
                Symmetry precision to use for determining symmetry operations
                and thus point symmetries with ``spglib``, for the `unrelaxed`
                (bulk site) point symmetry. Default in ``doped`` is ``0.01``
                which matches the ``pymatgen`` default. You may want to adjust
                for your system (e.g. if there are very slight octahedral
                distortions etc.). If set, then site symmetries & degeneracies
                will be re-parsed/computed even if already present in the
                ``DefectEntry`` object ``calculation_metadata``.
                If ``fixed_symprec_and_dist_tol_factor`` is ``False``
                (default), this value will be automatically adjusted (up to
                10x, down to 0.1x) until the identified equivalent sites from
                ``spglib`` have consistent point group symmetries. Setting
                ``verbose`` to ``True`` will print information on the trialled
                ``symprec`` (and ``dist_tol_factor`` values).
                (Default: None)
            **kwargs:
                Additional keyword arguments to pass to
                ``get_all_equiv_sites`` /
                ``get_equiv_frac_coords_in_primitive``, such as
                ``dist_tol_factor``, ``fixed_symprec_and_dist_tol_factor``, and
                ``verbose``, and/or ``Defect`` initialization (such as
                ``oxi_state``, ``multiplicity``, ``dist_tol_factor``) in the
                ``defect_and_info_from_structures`` function.
        """
        from doped.utils.parsing import (
            _num_electrons_from_charge_state,
            _simple_spin_degeneracy_from_num_electrons,
        )
        from doped.utils.symmetry import get_orientational_degeneracy, point_symmetry_from_defect_entry

        reparse = symprec is not None or bulk_symprec is not None
        if "relaxed point symmetry" not in self.calculation_metadata or reparse:
            try:
                point_symm_and_periodicity_breaking = point_symmetry_from_defect_entry(
                    self,
                    relaxed=True,
                    return_periodicity_breaking=True,
                    verbose=kwargs.get("verbose", False),
                    symprec=symprec,
                    **{
                        k: v
                        for k, v in kwargs.items()
                        if k in ["dist_tol_factor", "fixed_symprec_and_dist_tol_factor"]
                    },
                )
                assert isinstance(point_symm_and_periodicity_breaking, tuple)  # typing (tuple returned)
                (
                    self.calculation_metadata["relaxed point symmetry"],
                    self.calculation_metadata["periodicity_breaking_supercell"],
                ) = point_symm_and_periodicity_breaking

            except Exception as e:
                warnings.warn(
                    f"Unable to determine relaxed point group symmetry for {self.name}, got error:\n{e!r}"
                )
        if "bulk site symmetry" not in self.calculation_metadata or reparse:
            try:
                self.calculation_metadata["bulk site symmetry"] = point_symmetry_from_defect_entry(
                    self,
                    relaxed=False,
                    symprec=bulk_symprec,
                    **{
                        k: v
                        for k, v in kwargs.items()
                        if k in ["dist_tol_factor", "fixed_symprec_and_dist_tol_factor", "verbose"]
                    },
                )
            except Exception as e:
                warnings.warn(f"Unable to determine bulk site symmetry for {self.name}, got error:\n{e!r}")

            from doped.utils.parsing import _update_defect_entry_structure_metadata

            structure_metadata_kwargs = kwargs
            if bulk_symprec is not None:  # only include if not None
                structure_metadata_kwargs["symprec"] = bulk_symprec
            _update_defect_entry_structure_metadata(
                self,
                overwrite=True,
                **structure_metadata_kwargs,
            )  # re-determines site positions / multiplicities

        if (
            all(x in self.calculation_metadata for x in ["relaxed point symmetry", "bulk site symmetry"])
            and "orientational degeneracy" not in self.degeneracy_factors
        ) or reparse:
            try:
                self.degeneracy_factors["orientational degeneracy"] = get_orientational_degeneracy(
                    relaxed_point_group=self.calculation_metadata["relaxed point symmetry"],
                    bulk_site_point_group=self.calculation_metadata["bulk site symmetry"],
                    symprec=symprec or 0.1,
                    bulk_symprec=bulk_symprec or 0.01,
                    **{
                        k: v
                        for k, v in kwargs.items()
                        if k in ["dist_tol_factor", "fixed_symprec_and_dist_tol_factor", "verbose"]
                    },
                )
            except Exception as e:
                warnings.warn(
                    f"Unable to determine orientational degeneracy for {self.name}, got error:\n{e!r}"
                )

        if "spin degeneracy" not in self.degeneracy_factors:  # if not set, use simple spin degeneracy
            try:
                self.degeneracy_factors["spin degeneracy"] = _simple_spin_degeneracy_from_num_electrons(
                    _num_electrons_from_charge_state(self.defect_supercell, self.charge_state)
                )
            except Exception as e:
                warnings.warn(f"Unable to determine spin degeneracy for {self.name}, got error:\n{e!r}")

    def equilibrium_concentration(
        self,
        temperature: float = 300,
        chempots: dict | None = None,
        limit: str | None = None,
        el_refs: dict | None = None,
        fermi_level: float = 0,
        vbm: float | None = None,
        per_site: bool = False,
        symprec: float | None = None,
        formation_energy: float | None = None,
        site_competition: bool = True,
        **kwargs,
    ) -> float:
        r"""
        Compute the `equilibrium` concentration (in cm^-3) for the
        ``DefectEntry`` at a given chemical potential limit, fermi_level and
        temperature, assuming the dilute limit approximation.

        Note that these are the `equilibrium` defect concentrations!
        ``DefectThermodynamics.get_fermi_level_and_concentrations()`` can
        instead be used to calculate the Fermi level and defect concentrations
        for a material grown/annealed at higher temperatures and then cooled
        (quenched) to room/operating temperature (where defect concentrations
        are assumed to remain fixed) -- this is known as the frozen defect
        approach and is typically the most valid approximation (see its
        docstring for more information, and discussion in 10.1039/D3CS00432E).

        The degeneracy/multiplicity factor "g" is an important parameter in the
        defect concentration equation, affecting the final concentration by up
        to 2 orders of magnitude. This factor is taken from the product of the
        ``defect_entry.defect.multiplicity`` and
        ``defect_entry.degeneracy_factors`` attributes. See discussion in:
        https://doi.org/10.1039/D2FD00043A, https://doi.org/10.1039/D3CS00432E.

        Args:
            temperature (float):
                Temperature in Kelvin at which to calculate the equilibrium
                concentration. Default is 300 K.
            chempots (dict):
                Dictionary of chemical potentials to use for calculating the
                defect formation energy (and thus concentration). This can have
                the form of: ``{"limits": [{'limit': [chempot_dict]}]}`` (the
                format generated by ``doped``\'s chemical potential parsing
                functions (see tutorials)) and specific limits (chemical
                potential limits) can then be chosen using ``limit``.

                Alternatively this can be a dictionary of chemical potentials
                for a single limit, in the format:
                ``{element symbol: chemical potential}``.
                If manually specifying chemical potentials this way, you can
                set the ``el_refs`` option with the DFT reference energies of
                the elemental phases, in which case it is the formal chemical
                potentials (i.e. relative to the elemental references) that
                should be given here, otherwise the absolute (DFT) chemical
                potentials should be given.

                If ``None`` (default), sets all chemical potentials to 0
                (inaccurate formation energies and concentrations!).
            limit (str):
                The chemical potential limit for which to
                calculate the formation energy and thus concentration. Can be:

                - ``None``, default if ``chempots`` corresponds to a single
                  chemical potential limit -- otherwise will use the first
                  chemical potential limit in the ``chempots`` dict.
                - "X-rich"/"X-poor" where X is an element in the system, in
                  which case the most X-rich/poor limit will be used (e.g.
                  "Li-rich").
                - A key in the ``(self.)chempots["limits"]`` dictionary.

                The latter two options can only be used if ``chempots`` is in
                the ``doped`` format (see chemical potentials tutorial).
                (Default: None)
            el_refs (dict):
                Dictionary of elemental reference energies for the chemical
                potentials in the format:
                ``{element symbol: reference energy}`` (to determine the formal
                chemical potentials, when ``chempots`` has been manually
                specified as ``{element symbol: chemical potential}``).
                Unnecessary if ``chempots`` is provided/present in format
                generated by ``doped`` (see tutorials).
                (Default: None)
            vbm (float):
                VBM eigenvalue to use as Fermi level reference point for
                calculating the formation energy. If ``None`` (default), will
                use ``"vbm"`` from the ``calculation_metadata`` dict attribute
                if present -- which corresponds to the VBM of the
                `bulk supercell` calculation by default, unless
                ``bulk_band_gap_vr`` is set during defect parsing.
            fermi_level (float):
                Value corresponding to the electron chemical potential,
                referenced to the VBM. Default is 0 (i.e. the VBM).
            per_site (bool):
                Whether to return the concentration as fractional concentration
                per site, rather than the default of per cm^3. Multiply by 100
                for concentration in percent. Default is ``False``.
            symprec (float):
                Symmetry tolerance for ``spglib`` to use when determining
                `relaxed` defect point symmetries and thus orientational
                degeneracies. Default in ``doped`` is ``0.1`` which matches
                that used by the ``Materials Project`` and is larger than the
                ``pymatgen`` default of ``0.01`` to account for residual
                structural noise in relaxed defect supercells. If set, then
                site symmetries & degeneracies will be re-parsed/computed even
                if already present in the ``DefectEntry`` object
                ``calculation_metadata``. You may want to adjust for your
                system (e.g. if there are very slight octahedral distortions
                etc.).
            formation_energy (float):
                Pre-calculated formation energy to use for the defect
                concentration calculation, in order to reduce compute times
                (e.g. when looping over many chemical potential / temperature
                / etc ranges). Only really intended for internal ``doped``
                usage. If ``None`` (default), will calculate the formation
                energy using the input ``chempots``, ``limit``, ``el_refs``,
                ``vbm`` and ``fermi_level`` arguments. (Default: None)
            site_competition (bool):
                If ``True`` (default), uses the updated Fermi-Dirac-like
                formula for defect concentration, which accounts for defect
                site competition at high concentrations (see Kasamatsu et al.
                (10.1016/j.ssi.2010.11.022) appendix for derivation -- updated
                here to additionally account for configurational degeneracies
                ``g`` (see https://doi.org/10.1039/D3CS00432E)), which gives
                the following defect concentration equation:
                ``N_X = N*[g*exp(-E/kT) / (1 + sum(g_i*exp(-E_i/kT)))]``
                (https://doi.org/10.26434/chemrxiv-2025-j44qd) where ``i`` runs
                over all defects which occupy the same site as the defect of
                interest. Otherwise, uses the standard dilute limit
                approximation. Note that when used with
                ``DefectEntry.equilibrium_concentration()`` here, only this
                defect itself is considered in the sum over ``i`` in the
                denominator (as it has no knowledge of other defect
                concentrations), but if used with
                ``DefectThermodynamics.get_equilibrium_concentrations()`` or
                ``DefectThermodynamics.get_fermi_level_and_concentrations()``
                (recommended) then all defects in the system occupying the same
                lattice site are considered.
            **kwargs:
                Additional keyword arguments to pass to
                ``_parse_and_set_symmetries_and_degeneracies``, such as
                ``bulk_symprec``, ``symprec``, ``dist_tol_factor`` etc.

        Returns:
            float:
                Concentration in cm^-3 (or as fractional per site, if
                ``per_site`` is ``True``).
        """
        self._parse_and_set_symmetries_and_degeneracies(symprec=symprec, **kwargs)

        if "spin degeneracy" not in self.degeneracy_factors:
            warnings.warn(
                "'spin degeneracy' is not defined in the DefectEntry degeneracy_factors attribute. "
                "This factor contributes to the degeneracy term 'g' in the defect concentration equation "
                "(N_X = N*g*exp(-E/kT)) and is automatically computed when parsing with doped "
                "(see discussion in doi.org/10.1039/D2FD00043A and doi.org/10.1039/D3CS00432E). This will "
                "affect the computed defect concentration / Fermi level!\n"
                "To avoid this, you can (re-)parse your defect(s) with doped, or manually set "
                "'spin degeneracy' in the degeneracy_factors attribute(s) -- usually 2 for odd-electron "
                "defect species and 1 for even-electron)."
            )

        if (
            "orientational degeneracy" not in self.degeneracy_factors
            and self.defect.defect_type != core.DefectType.Interstitial
        ):
            warnings.warn(
                "'orientational degeneracy' is not defined in the DefectEntry degeneracy_factors "
                "attribute (for this vacancy/substitution defect). This factor contributes to the "
                "degeneracy term 'g' in the defect concentration equation (N_X = N*g*exp(-E/kT) -- see "
                "discussion in doi.org/10.1039/D2FD00043A and doi.org/10.1039/D3CS00432E) and is "
                "automatically computed when parsing with doped if possible (if the defect supercell "
                "doesn't break the host periodicity). This will affect the computed defect concentrations "
                "/ Fermi level!\n"
                "To avoid this, you can (re-)parse your defects with doped (if not tried already), or "
                "manually set 'orientational degeneracy' in the degeneracy_factors attribute(s)."
            )

        if self.calculation_metadata.get("periodicity_breaking_supercell", False):
            warnings.warn(_orientational_degeneracy_warning)

        if formation_energy is None:
            formation_energy = self.formation_energy(  # if chempots is None, this will throw warning
                chempots=chempots, limit=limit, el_refs=el_refs, vbm=vbm, fermi_level=fermi_level
            )

        with np.errstate(over="ignore"):
            exp_factor = np.exp(
                -formation_energy / (constants_value("Boltzmann constant in eV/K") * temperature)
            )

            degeneracy_factor = (
                np.prod(list(self.degeneracy_factors.values())) if self.degeneracy_factors else 1
            )
            per_site_concentration = exp_factor * degeneracy_factor
            if site_competition:
                per_site_concentration /= 1 + per_site_concentration

            if per_site:
                return per_site_concentration

            return self.bulk_site_concentration * per_site_concentration

    @property
    def bulk_site_concentration(self):
        """
        Return the site concentration (in cm^-3) of the corresponding atomic
        site of the defect in the pristine bulk material (e.g. if the defect is
        V_O in SrTiO3, returns the site concentration of (symmetry-equivalent)
        oxygen atoms in SrTiO3).
        """
        volume_in_cm3 = self.defect.volume * 1e-24  # convert volume in Å^3 to cm^3
        return self.defect.multiplicity / volume_in_cm3

    def __repr__(self):
        """
        Returns a string representation of the ``DefectEntry`` object.
        """
        from doped.utils.parsing import _get_bulk_supercell

        bulk_supercell = _get_bulk_supercell(self)
        try:
            defect_name = self.defect.name
            if bulk_supercell is not None:
                formula = bulk_supercell.composition.get_reduced_formula_and_factor(iupac_ordering=True)[0]
            else:
                formula = self.defect.structure.composition.get_reduced_formula_and_factor(
                    iupac_ordering=True
                )[0]
        except AttributeError:
            defect_name = "unknown"
            formula = "unknown"

        properties, methods = _doped_obj_properties_methods(self)
        return (
            f"doped DefectEntry: {self.name}, with bulk composition: {formula} and defect: {defect_name}.\n"
            f"Available attributes:\n{properties}\n\nAvailable methods:\n{methods}"
        )

    def __eq__(self, other) -> bool:
        """
        Determine whether two ``DefectEntry`` objects are equal, by comparing
        ``self.name``, ``self.sc_entry_energy``, ``self.bulk_entry_energy`` and
        ``self.corrections`` (i.e. name and energy match).
        """
        return (
            self.name == other.name
            and self.sc_entry_energy == other.sc_entry_energy
            and self.bulk_entry_energy == other.bulk_entry_energy
            and self.corrections == other.corrections
        )

    def __hash__(self):
        """
        Hash the ``DefectEntry`` object by its name, supercell energy, bulk
        energy and corrections (i.e. defined by name and energy, as in the
        ``__eq__`` method).
        """
        return hash(
            (
                self.name,
                self.sc_entry_energy,
                self.bulk_entry_energy,
                tuple(sorted(self.corrections.values())),
            )
        )

    @property
    def bulk_entry_energy(self):
        r"""
        Get the raw energy of the bulk supercell calculation (i.e.
        ``bulk_entry.energy``).

        Refactored from ``pymatgen-analysis-defects`` to be more efficient,
        reducing compute times when looping over formation energy /
        concentration functions.
        """
        if self.bulk_entry is None:
            return None

        if hasattr(self, "_bulk_entry_energy") and self._bulk_entry_hash == hash(self.bulk_entry):
            return self._bulk_entry_energy

        self._bulk_entry_hash = hash(self.bulk_entry)
        self._bulk_entry_energy = self.bulk_entry.energy

        return self._bulk_entry_energy

    @property
    def sc_entry_energy(self):
        r"""
        Get the raw energy of the defect supercell calculation (i.e.
        ``sc_entry.energy``).

        Refactored from ``pymatgen-analysis-defects`` to be more efficient,
        reducing compute times when looping over formation energy /
        concentration functions.
        """
        if hasattr(self, "_sc_entry_energy") and self._sc_entry_hash == hash(self.sc_entry):
            return self._sc_entry_energy

        self._sc_entry_hash = hash(self.sc_entry)
        self._sc_entry_energy = self.sc_entry.energy

        return self._sc_entry_energy

    @property
    def is_shallow(self) -> bool:
        """
        Whether the ``DefectEntry`` is determined to be a shallow (perturbed
        host) state, based on ``pydefect`` eigenvalue analysis, or not.
        """
        return is_shallow(self)

    def plot_site_displacements(
        self,
        separated_by_direction: bool = False,
        relaxed_distances: bool = False,
        relative_to_defect: bool = False,
        vector_to_project_on: list | None = None,
        use_plotly: bool = False,
        style_file: PathLike | None = "",
    ):
        """
        Plot the site displacements as a function of distance from the defect
        site.

        Set ``use_plotly = True`` to get an interactive ``plotly`` plot, useful
        for analysis!

        Args:
            separated_by_direction (bool):
                Whether to plot the site displacements separated by the
                x, y and z directions (True) or all together (False).
                Defaults to False.
            relaxed_distances (bool):
                Whether to use the atomic positions in the `relaxed` defect
                supercell for ``'Distance to defect'``,
                ``'Vector to site from defect'`` and
                ``'Displacement wrt defect'`` values (``True``), or unrelaxed
                positions (i.e. the bulk structure positions)(``False``).
                Defaults to ``False``.
            relative_to_defect (bool):
                Whether to plot the signed displacements along the line from
                the defect site to that atom. Negative values indicate the atom
                moves towards the defect (compressive strain), positive values
                indicate the atom moves away from the defect (tensile strain).
                Uses the *relaxed* defect position as reference.
            vector_to_project_on:
                Direction to project the site displacements along
                (e.g. [0, 0, 1]). Defaults to ``None`` (displacements are given
                as vectors in Cartesian space).
            use_plotly (bool):
                Whether to use ``plotly`` (``True``) or ``matplotlib``
                (``False``; default). Set to ``True`` to get an interactive
                plot.
            style_file (PathLike):
                Path to a ``matplotlib`` style file to use for the plot. If
                ``None`` (default), uses the default ``doped`` style file.
        """
        from doped.utils.displacements import plot_site_displacements

        return plot_site_displacements(
            defect_entry=self,
            separated_by_direction=separated_by_direction,
            relaxed_distances=relaxed_distances,
            relative_to_defect=relative_to_defect,
            vector_to_project_on=vector_to_project_on,
            use_plotly=use_plotly,
            style_file=style_file,
        )


def is_shallow(defect_entry: DefectEntry, default: bool = False) -> bool:
    """
    Return whether a ``DefectEntry`` is determined to be a shallow (perturbed
    host) state, based on ``pydefect`` eigenvalue analysis.

    Args:
        defect_entry (DefectEntry):
            ``doped`` ``DefectEntry`` object.
        default (bool):
            Default value to return if the eigenvalue analysis fails
            (e.g. if eigenvalue data is not present).
            Default is ``False``.
    """
    try:
        return defect_entry.get_eigenvalue_analysis(plot=False).is_shallow  # type: ignore
    except Exception:
        return default


def _parse_procar(procar: PathLike | Procar | None = None):
    """
    Parse the input path or ``pymatgen`` ``Procar`` to a ``Procar`` object in
    the correct format, for eigenvalue analysis.

    Args:
        procar (PathLike, Procar):
            Either a path to the ``VASP`` ``PROCAR``` output file (with
            ``LORBIT > 10`` in the ``INCAR``) or a``pymatgen`` ``Procar``.

    Returns:
        Procar: The parsed ``Procar`` object in ``pymatgen`` format.
    """
    from pymatgen.electronic_structure.core import Spin

    from doped.utils.parsing import get_procar

    if not hasattr(procar, "data"):  # not a parsed Procar object
        if procar and hasattr(procar, "proj_data") and not isinstance(procar, PathLike | Procar):
            if procar._is_soc:
                procar.data = {Spin.up: procar.proj_data[0]}
            else:
                procar.data = {Spin.up: procar.proj_data[0], Spin.down: procar.proj_data[1]}
            del procar.proj_data

        elif isinstance(procar, PathLike):  # path to PROCAR file
            procar = get_procar(procar)

    return procar


def _no_chempots_warning(property="Formation energies (and concentrations)"):
    warnings.warn(
        f"No chemical potentials supplied, so using 0 for all chemical potentials. {property} will likely "
        f"be highly inaccurate!"
    )


def _get_dft_chempots(chempots: dict | None, el_refs: dict | None = None, limit: str | None = None):
    """
    Parse the DFT chempots from the input chempots and limit.
    """
    from doped.thermodynamics import _parse_chempots, _parse_limit

    chempots, _el_refs = _parse_chempots(chempots, el_refs, update_el_refs=True)
    if chempots is not None:
        limit = _parse_limit(chempots, limit)
        if limit is None:
            limit = next(iter(chempots["limits"].keys()))
            if len(chempots["limits"]) > 1:  # more than 1 limit, so warn
                warnings.warn(
                    f"No chemical potential limit specified! Using {limit} for computing the "
                    f"formation energy"
                )

    elif limit is not None:
        warnings.warn(
            "You have specified a chemical potential limit but no chemical potentials "
            "(`chempots`) were supplied, so `limit` will be ignored."
        )

    if limit is not None and chempots is not None:
        return chempots["limits"][limit]

    return chempots


def _guess_and_set_struct_oxi_states(structure):
    """
    Tries to guess (and set) the oxidation states of the input structure, using
    the ``pymatgen`` ``BVAnalyzer`` class.

    If a single-element structure is passed, the oxidation state is assumed to
    be zero (no mixed-valence single-element systems that I know of, would be
    pretty wild).

    Args:
        structure (Structure):
            The structure for which to guess the oxidation states.

    Returns:
        Structure:
            The structure with oxidation states guessed and set, or ``False``
            if oxidation states could not be guessed.
    """
    if len(structure.composition.elements) == 1:
        oxi_dec_structure = structure.copy()  # don't modify original structure
        oxi_dec_structure.add_oxidation_state_by_element(
            {next(iter(structure.composition.elements)).symbol: 0}
        )
        return oxi_dec_structure

    for symm_tol in [0.1, 0]:  # default, then with no symmetry
        with contextlib.suppress(ValueError):
            bv_analyzer = BVAnalyzer(symm_tol=symm_tol)
            # ValueError raised if oxi states can't be assigned
            oxi_dec_structure = bv_analyzer.get_oxi_state_decorated_structure(structure)
            if all(
                np.isclose(int(specie.oxi_state), specie.oxi_state) for specie in oxi_dec_structure.species
            ):
                return oxi_dec_structure

    return False  # if oxi states could not be guessed


def _guess_and_set_struct_oxi_states_icsd_prob(structure, try_without_max_sites=False):
    """
    Tries to guess (and set) the oxidation states of the input structure, using
    the ``pymatgen``-tabulated ICSD oxidation state probabilities.

    Args:
        structure (Structure):
            The structure for which to guess the oxidation states.
        try_without_max_sites (bool):
            Whether to try to guess the oxidation states
            without using the ``max_sites=-1`` argument (``True``)(which
            attempts to use the reduced composition for guessing oxi states) or
            not (``False``; default).

    Returns:
        Structure:
            The structure with oxidation states guessed and set, or ``False``
            if oxidation states could not be guessed.
    """
    structure = structure.copy()  # don't modify original structure
    if try_without_max_sites:
        with contextlib.suppress(Exception):
            structure.add_oxidation_state_by_guess()
            # check all oxidation states are whole numbers:
            if all(np.isclose(int(specie.oxi_state), specie.oxi_state) for specie in structure.species):
                return structure

    # else try to use the reduced cell since oxidation state assignment scales poorly with system size:
    try:
        attempt = 0
        structure.add_oxidation_state_by_guess(max_sites=-1)
        while (  # check oxi_states assigned and not all zero:
            attempt < 3
            and all(specie.oxi_state == 0 for specie in structure.species)
            or not all(np.isclose(int(specie.oxi_state), specie.oxi_state) for specie in structure.species)
        ):
            attempt += 1
            if attempt == 1:
                structure.add_oxidation_state_by_guess(max_sites=-1, all_oxi_states=True)
            elif attempt == 2:
                structure.add_oxidation_state_by_guess()
    except Exception:
        structure.add_oxidation_state_by_guess()

    if all(hasattr(site.specie, "oxi_state") for site in structure.sites) and all(
        isinstance(site.specie.oxi_state, int | float) for site in structure.sites
    ):
        return structure

    return False


def guess_and_set_struct_oxi_states(structure, try_without_max_sites=False):
    """
    Tries to guess (and set) the oxidation states of the input structure, first
    using the ``pymatgen`` ``BVAnalyzer`` class, and if that fails, using the
    ICSD oxidation state probabilities to guess.

    Args:
        structure (Structure):
            The structure for which to guess the oxidation states.
        try_without_max_sites (bool):
            Whether to try to guess the oxidation states
            without using the ``max_sites=-1`` argument (``True``)(which
            attempts to use the reduced composition for guessing oxi states) or
            not (``False``; default), when using the ICSD oxidation state
            probability guessing.

    Returns:
        Structure: The structure with oxidation states guessed and set, or ``False``
        if oxidation states could not be guessed.
    """
    if structure_with_oxi := _guess_and_set_struct_oxi_states(structure):
        return structure_with_oxi

    return _guess_and_set_struct_oxi_states_icsd_prob(structure, try_without_max_sites)


def guess_and_set_oxi_states_with_timeout(
    structure, timeout_1=10, timeout_2=15, break_early_if_expensive=False
) -> bool:
    """
    Tries to guess (and set) the oxidation states of the input structure, with
    a timeout catch for cases where the structure is complex and oxi state
    guessing will take a very very long time.

    Tries first without using the ``pymatgen`` ``BVAnalyzer`` class, and if
    this fails, tries using the ICSD oxidation state probabilities (with
    timeouts) to guess.

    Args:
        structure (Structure):
            The structure for which to guess the oxidation states.
        timeout_1 (float):
            Timeout in seconds for the second attempt to guess the oxidation
            states, using ICSD oxidation state probabilities (with
            ``max_sites=-1``). Default is 10 seconds.
        timeout_2 (float):
            Timeout in seconds for the third attempt to guess the oxidation
            states, using ICSD oxidation state probabilities (without
            ``max_sites=-1``). Default is 15 seconds.
        break_early_if_expensive (bool):
            Whether to stop the function if the first oxi state guessing
            attempt (with ``BVAnalyzer``) fails and the cost estimate for the
            ICSD probability guessing is high (expected to take a long time;
            > 10 seconds). Default is ``False``.

    Returns:
        Structure:
            The structure with oxidation states guessed and set, or ``False``
            if oxidation states could not be guessed.
    """
    if structure_with_oxi := _guess_and_set_struct_oxi_states(structure):
        return structure_with_oxi  # BVAnalyzer succeeded

    if (  # if BVAnalyzer failed and cost estimate is high, break early:
        (
            break_early_if_expensive or mp.current_process().daemon
        )  # if in a daemon process, can't spawn new `Process`s
        and _rough_oxi_state_cost_icsd_prob_from_comp(structure.composition) > 1e6
    ):
        return False

    if mp.current_process().daemon:  # if in a daemon process, can't spawn new `Process`s
        return _guess_and_set_struct_oxi_states_icsd_prob(structure)

    return _guess_and_set_oxi_states_with_timeout_icsd_prob(structure, timeout_1, timeout_2)


def _guess_and_set_struct_oxi_states_icsd_prob_process(structure, queue, try_without_max_sites=False):
    """
    Implements the ``_guess_and_set_struct_oxi_states_icsd_prob`` function
    above, but also putting the results into the supplied ``multiprocessing``
    queue object (for use with timeouts via ``Process``).

    For internal ``doped`` usage.
    """
    if structure_with_oxi := _guess_and_set_struct_oxi_states_icsd_prob(structure, try_without_max_sites):
        queue.put(structure_with_oxi)
    else:
        queue.put(False)


def _guess_and_set_oxi_states_with_timeout_icsd_prob(
    structure,
    timeout_1: float = 10,
    timeout_2: float = 15,
) -> bool:
    """
    Tries to guess (and set) the oxidation states of the input structure using
    the ICSD oxidation state probabilities approach, with a timeout catch for
    cases where the structure is complex and oxi state guessing will take a
    very very long time.

    Tries first without using the ``max_sites=-1`` argument with ``pymatgen``'s
    oxidation state guessing functions (which attempts to use the reduced
    composition for guessing oxi states, but can be a little less reliable for
    tricky cases), and if that times out, tries without ``max_sites=-1``.

    Args:
        structure (Structure):
            The structure for which to guess the oxidation states.
        timeout_1 (float):
            Timeout in seconds for the first attempt to guess the oxidation
            states (with ``max_sites=-1``). Default is 10 seconds.
        timeout_2 (float):
            Timeout in seconds for the second attempt to guess the oxidation
            states (without ``max_sites=-1``). Default is 15 seconds.

    Returns:
        Structure:
            The structure with oxidation states guessed and set, or ``False``
            if oxidation states could not be guessed.
    """
    queue = mp.SimpleQueue()

    guess_oxi_process_wout_max_sites = mp.Process(
        target=_guess_and_set_struct_oxi_states_icsd_prob_process, args=(structure, queue, True)
    )  # try without max sites first, if fails, try with max sites
    guess_oxi_process_wout_max_sites.start()
    guess_oxi_process_wout_max_sites.join(timeout=timeout_1)

    if guess_oxi_process_wout_max_sites.is_alive():  # still running, revert to using max sites
        guess_oxi_process_wout_max_sites.terminate()
        guess_oxi_process_wout_max_sites.join()

        guess_oxi_process = mp.Process(
            target=_guess_and_set_struct_oxi_states_icsd_prob_process,
            args=(structure, queue, False),
        )
        guess_oxi_process.start()
        guess_oxi_process.join(timeout=timeout_2)  # wait for pymatgen to guess oxi states,
        # otherwise revert to all Defect oxi states being set to 0

        if guess_oxi_process.is_alive():
            guess_oxi_process.terminate()
            guess_oxi_process.join()

            return False

    # apply oxi states to structure:
    return queue.get()


def _rough_oxi_state_cost_icsd_prob_from_comp(comp: str | Composition, max_sites=True) -> float:
    """
    A cost function which roughly estimates the computational cost of guessing
    the oxidation states of a given composition, using the ICSD oxidation state
    probabilities approach.
    """
    if isinstance(comp, str):
        comp = Composition(comp)

    if max_sites:
        comp, _factor = comp.get_reduced_composition_and_factor()

    el_amt = comp.get_el_amt_dict()
    elements = list(el_amt)

    def num_possible_combinations(n, r):
        from math import factorial

        return factorial(n + r - 1) / factorial(r) / factorial(n - 1)

    return np.prod(
        [
            num_possible_combinations(
                len(Element(el).icsd_oxidation_states or Element(el).oxidation_states), int(el_amt[el])
            )
            for el in elements
        ]
    )


class Defect(core.Defect):
    """
    ``doped`` ``Defect`` object.
    """

    def __init__(
        self,
        structure: Structure,
        site: PeriodicSite,
        multiplicity: int | None = None,
        oxi_state: float | str | None = None,
        equivalent_sites: list[PeriodicSite] | None = None,
        symprec: float = 0.01,
        angle_tolerance: float = 5,
        user_charges: list[int] | None = None,
        **doped_kwargs,
    ):
        """
        Subclass of ``pymatgen.analysis.defects.core.Defect`` with additional
        attributes and methods used by ``doped``.

        Args:
            structure (Structure):
                The structure in which to create the defect. Typically
                the primitive structure of the host crystal for defect
                generation, and/or the calculation supercell for defect
                parsing.
            site (PeriodicSite):
                The defect site in the structure.
            multiplicity (int):
                The multiplicity of the defect in the structure.
            oxi_state (float, int or str):
                The oxidation state of the defect. If not specified,
                this will be determined automatically.
            equivalent_sites (list[PeriodicSite]):
                A list of equivalent sites for the defect in the structure.
            symprec (float):
                Symmetry tolerance for identifying equivalent sites.
                Default is ``0.01``.
            angle_tolerance (float):
                Angle tolerance for identifying equivalent sites.
                Default is ``5``.
            user_charges (list[int]):
                User specified charge states. If specified,
                ``get_charge_states`` will return this list. If ``None`` or
                an empty list, the charge states will be determined
                automatically.
            **doped_kwargs:
                Additional keyword arguments to define doped-specific
                attributes (listed below), in the form
                ``doped_attribute_name=value``; (e.g. ``wyckoff = "4a"``).
        """
        super().__init__(
            structure=structure,
            site=site.to_unit_cell(),  # ensure mapped to unit cell
            multiplicity=multiplicity,
            oxi_state=0,  # set oxi_state in more efficient and robust way below (crashes for large
            # input structures)
            equivalent_sites=(
                [site.to_unit_cell() for site in equivalent_sites]
                if equivalent_sites is not None
                else None
            ),
            symprec=symprec,
            angle_tolerance=angle_tolerance,
            user_charges=user_charges,
        )  # core attributes

        if oxi_state is None:
            self._set_oxi_state()
        else:
            self.oxi_state = oxi_state

        self.conventional_structure: Structure | None = doped_kwargs.get("conventional_structure", None)
        self.conv_cell_frac_coords: np.ndarray | None = doped_kwargs.get("conv_cell_frac_coords", None)
        self.equiv_conv_cell_frac_coords: list[np.ndarray] = doped_kwargs.get(
            "equiv_conv_cell_frac_coords", []
        )
        self._BilbaoCS_conv_cell_vector_mapping: list[int] = doped_kwargs.get(
            "_BilbaoCS_conv_cell_vector_mapping", [0, 1, 2]
        )
        self.wyckoff: str | None = doped_kwargs.get("wyckoff", None)

    def _set_oxi_state(self):
        # only try guessing bulk oxi states if not already set:
        if not (
            all(hasattr(site.specie, "oxi_state") for site in self.structure.sites)
            and all(isinstance(site.specie.oxi_state, int | float) for site in self.structure.sites)
        ):
            # try guess oxi-states but with timeout:
            if struct_w_oxi := guess_and_set_oxi_states_with_timeout(
                self.structure, timeout_1=5, timeout_2=5, break_early_if_expensive=True
            ):
                self.structure = struct_w_oxi
                if self.defect_type != core.DefectType.Interstitial:
                    self._defect_site = min(
                        self.structure.get_sites_in_sphere(
                            self.site.coords,
                            0.5,
                        ),
                        key=lambda x: x[1],
                    )
            else:
                self.oxi_state = "Undetermined"
                return

        self.oxi_state = self._guess_oxi_state()

    @classmethod
    def _from_pmg_defect(
        cls,
        defect: core.Defect,
        bulk_oxi_states: Structure | Composition | dict | bool = False,
        **doped_kwargs,
    ) -> "Defect":
        """
        Create a ``doped`` ``Defect`` from a ``pymatgen`` ``Defect`` object.

        Args:
            defect:
                ``pymatgen`` ``Defect`` object.
            bulk_oxi_states:
                Controls oxi-state guessing (later used for charge state
                guessing). By default, oxidation states are taken from
                ``doped_kwargs['oxi_state']`` if set, otherwise from
                ``bulk_oxi_states`` which can be either a ``pymatgen``
                ``Structure`` or ``Composition`` object, or a dict (of
                ``{element: oxi_state}``), or otherwise guessed using the
                ``doped`` methods.
                If ``bulk_oxi_states`` is ``False``, then just uses the
                already-set ``Defect`` ``oxi_state`` attribute (default = 0),
                with no more guessing.
                If ``True``, re-guesses the oxidation state of the defect
                (ignoring the ``pymatgen`` ``Defect``  ``oxi_state``
                attribute).

                If the structure is mixed-valence, then ``bulk_oxi_states``
                should be either a structure input or ``True`` (to re-guess).

                Default behaviour in ``doped`` generation is to provide
                ``bulk_oxi_states`` as an oxi-state decorated ``Structure``, to
                make defect setup more robust and efficient (particularly for
                odd input structures, such as defect supercells etc). Oxidation
                states are removed from structures in the ``pymatgen`` defect
                generation functions, so this allows us to re-add them after.
            **doped_kwargs:
                Additional keyword arguments to define doped-specific
                attributes (see class docstring).
        """
        # get doped kwargs from defect attributes, if defined:
        for doped_attr in [
            "conventional_structure",
            "conv_cell_frac_coords",
            "equiv_conv_cell_frac_coords",
            "_BilbaoCS_conv_cell_vector_mapping",
            "wyckoff",
        ]:
            if (
                hasattr(defect, doped_attr)
                and getattr(defect, doped_attr) is not None
                and doped_attr not in doped_kwargs
            ):
                doped_kwargs[doped_attr] = getattr(defect, doped_attr)

        oxi_state = None
        if doped_kwargs.get("oxi_state", False):
            oxi_state = doped_kwargs.pop("oxi_state")

        if oxi_state is None and isinstance(bulk_oxi_states, Structure):
            # if input structure was oxi-state-decorated, use these oxi states for defect generation:
            if not all(
                hasattr(site.specie, "oxi_state") and isinstance(site.specie.oxi_state, int | float)
                for site in bulk_oxi_states.sites
            ):
                warnings.warn(
                    "Input structure for ``bulk_oxi_states`` is not oxi-state decorated. "
                    "Setting ``bulk_oxi_states`` to ``True`` (i.e. re-guess oxi-states)."
                )
                bulk_oxi_states = True

            else:
                single_valence_oxi_states = {
                    el.symbol: el.oxi_state for el in bulk_oxi_states.composition.elements
                }
                if len(single_valence_oxi_states) == len(bulk_oxi_states.composition.elements):
                    bulk_oxi_states = single_valence_oxi_states

                else:  # otherwise it's mixed-valence! need to add oxi-states to structure
                    # first, if structures without oxi-states exactly match, then just use
                    # ``bulk_oxi_states`` structure:
                    defect_struct_wout_oxi = defect.structure.copy()
                    defect_struct_wout_oxi.remove_oxidation_states()
                    input_struct_wout_oxi = bulk_oxi_states.copy()
                    input_struct_wout_oxi.remove_oxidation_states()
                    if defect_struct_wout_oxi == input_struct_wout_oxi:
                        defect.structure = bulk_oxi_states

                    else:
                        from doped.utils.efficiency import StructureMatcher_scan_stol

                        mapping_to_defect = StructureMatcher_scan_stol(
                            defect.structure,
                            bulk_oxi_states,
                            func_name="get_mapping",
                            comparator=SpeciesComparator(),
                        )
                        if mapping_to_defect is None:
                            raise ValueError(
                                "Could not find a match between the defect and bulk oxi-state decorated "
                                "structure (in `bulk_oxi_states`). Please ensure the defect and bulk "
                                "structures are the same, or provide the bulk oxi-state decorated "
                                "structure directly."
                            )

                        site_oxi_states = np.zeros(len(defect.structure.sites))
                        for defect_struct_idx, oxi_dec_site in zip(
                            mapping_to_defect, bulk_oxi_states.sites, strict=False
                        ):
                            site_oxi_states[defect_struct_idx] = oxi_dec_site.specie.oxi_state
                        defect.structure.add_oxidation_state_by_site(site_oxi_states)

        if oxi_state is None and isinstance(bulk_oxi_states, Composition):
            try:
                bulk_oxi_states = {el.symbol: el.oxi_state for el in bulk_oxi_states.elements}
            except Exception as exc:
                warnings.warn(f"Could not extract oxidation states from Composition: {exc!r}")

            if len(bulk_oxi_states) != len(defect.structure.composition.elements):
                warnings.warn(
                    f"A mixed-valence Composition object was supplied for bulk_oxi_states, "
                    f"but only single-valence oxidation states can be extracted (use structure "
                    f"input if mixed-valence is required). Using {bulk_oxi_states}."
                )

        if oxi_state is None and isinstance(bulk_oxi_states, dict):
            req_elt_symbols = [elt.symbol for elt in defect.structure.composition.elements]
            if not all(i in req_elt_symbols for i in bulk_oxi_states):
                raise ValueError(
                    f"Input bulk_oxi_states {bulk_oxi_states} do not match all the elements in the defect "
                    f"structure {req_elt_symbols}."
                )
            defect.structure.add_oxidation_state_by_element(bulk_oxi_states)

        oxi_state = defect.oxi_state if oxi_state is None and not bulk_oxi_states else None

        return cls(
            structure=defect.structure,
            site=defect.site.to_unit_cell(),  # ensure mapped to unit cell
            multiplicity=defect.multiplicity,
            oxi_state=oxi_state,  # if still None, then taken from structure or re-guessed
            equivalent_sites=(
                [site.to_unit_cell() for site in defect.equivalent_sites]
                if defect.equivalent_sites is not None
                else None
            ),
            symprec=defect.symprec,
            angle_tolerance=defect.angle_tolerance,
            user_charges=defect.user_charges,
            **doped_kwargs,
        )

    def get_supercell_structure(
        self,
        sc_mat: np.ndarray | None = None,
        target_frac_coords: np.ndarray[float] | list[float] | None = None,
        return_sites: bool = False,
        min_image_distance: float = 10.0,  # same as current ``pymatgen`` default
        min_atoms: int = 50,  # different to current ``pymatgen`` default (80)
        force_cubic: bool = False,
        force_diagonal: bool = False,  # same as current ``pymatgen`` default
        ideal_threshold: float = 0.1,
        min_length: float | None = None,  # same as current ``pymatgen`` default, kept for compatibility
        dummy_species: str | None = None,
    ) -> Structure:
        """
        Generate the simulation supercell for a defect.

        Redefined from the parent class to allow the use of
        ``target_frac_coords`` to place the defect at the closest equivalent
        site to the target fractional coordinates in the supercell, while
        keeping the supercell fixed (to avoid any issues with defect parsing).
        Also returns information about equivalent defect sites in the
        supercell.

        If ``sc_mat`` is None, then the supercell is generated automatically
        using the ``doped`` algorithm described in the
        ``get_ideal_supercell_matrix`` function docstring in
        ``doped.generation``.

        Args:
            sc_mat (3x3 matrix):
                Transformation matrix of ``self.structure`` to create the
                supercell. If ``None`` (default), then automatically computed
                using ``get_ideal_supercell_matrix`` from ``doped.generation``.
            target_frac_coords (3x1 matrix):
                If set, the defect will be placed at the closest equivalent
                site to these fractional coordinates (using
                ``self.equivalent_sites``).
            return_sites (bool):
                If True, returns a tuple of the defect supercell, defect
                supercell site and list of equivalent supercell sites.
            dummy_species (str):
                Dummy species to highlight the defect position (for visualizing
                vacancies).
            min_image_distance (float):
                Minimum image distance in Å of the generated supercell (i.e.
                minimum distance between periodic images of atoms/sites in the
                lattice), if ``sc_mat`` is None.
                (Default = 10.0)
            min_atoms (int):
                Minimum number of atoms allowed in the generated supercell, if
                ``sc_mat`` is ``None``.
                (Default = 50)
            force_cubic (bool):
                Enforce usage of ``CubicSupercellTransformation`` from
                ``pymatgen`` for supercell generation (if ``sc_mat`` is
                ``None``). (Default = False)
            force_diagonal (bool):
                If True, return a transformation with a diagonal
                transformation matrix (if ``sc_mat`` is None).
                (Default = False)
            ideal_threshold (float):
                Threshold for increasing supercell size (beyond that which
                satisfies ``min_image_distance`` and `min_atoms``) to achieve
                an ideal supercell matrix (i.e. a diagonal expansion of the
                primitive or conventional cell). Supercells up to
                ``1 + perfect_cell_threshold`` times larger (rounded up) are
                trialled, and will instead be returned if they yield an ideal
                transformation matrix (if ``sc_mat`` is ``None``).
                (Default = 0.1; i.e. 10% larger than the minimum size)
            min_length (float):
                Same as ``min_image_distance`` (kept for compatibility).

        Returns:
            The defect supercell structure. If ``return_sites`` is True, also
            returns the defect supercell site and list of equivalent supercell
            sites.
        """
        if sc_mat is None:
            if min_length is not None:
                min_image_distance = min_length

            from doped.generation import get_ideal_supercell_matrix

            sc_mat = get_ideal_supercell_matrix(
                self.structure,
                min_image_distance=min_image_distance,
                min_atoms=min_atoms,
                ideal_threshold=ideal_threshold,
                force_cubic=force_cubic,
                force_diagonal=force_diagonal,
            )

        sites = self.equivalent_sites or [self.site]
        structure_w_all_defect_sites = Structure.from_sites(
            [PeriodicSite("X", site.frac_coords, self.structure.lattice) for site in sites]
        )
        sc_structure_w_all_defect_sites = structure_w_all_defect_sites * sc_mat
        equiv_sites = [
            PeriodicSite(self.site.specie, sc_x_site.frac_coords, sc_x_site.lattice).to_unit_cell()
            for sc_x_site in sc_structure_w_all_defect_sites
        ]

        if target_frac_coords is None:
            sc_structure = self.structure * sc_mat
            sc_mat_inv = np.linalg.inv(sc_mat)
            sc_pos = np.dot(self.site.frac_coords, sc_mat_inv)
            sc_site = PeriodicSite(self.site.specie, sc_pos, sc_structure.lattice).to_unit_cell()

        else:
            # sort by distance from target_frac_coords, then by magnitude of fractional coordinates:
            sc_site = sorted(
                equiv_sites,
                key=lambda site: (
                    round(
                        np.linalg.norm(site.frac_coords - np.array(target_frac_coords)),
                        4,
                    ),
                    round(np.linalg.norm(site.frac_coords), 4),
                    round(np.abs(site.frac_coords[0]), 4),
                    round(np.abs(site.frac_coords[1]), 4),
                    round(np.abs(site.frac_coords[2]), 4),
                ),
            )[0]

        sc_defect = self.__class__(
            structure=self.structure * sc_mat,
            site=sc_site,
            oxi_state=self.oxi_state,
            multiplicity=1,  # so doesn't break for interstitials
        )
        sc_defect_struct = sc_defect.defect_structure
        sc_defect_struct.remove_oxidation_states()

        # also remove oxidation states from sites:
        for site in [sc_site, *equiv_sites]:
            remove_site_oxi_state(site)

        if dummy_species is not None:
            sc_defect_struct.insert(len(self.structure * sc_mat), dummy_species, sc_site.frac_coords)

        from doped.utils.symmetry import _round_struct_coords

        sorted_sc_defect_struct = sc_defect_struct.get_sorted_structure()  # ensure proper sorting
        sorted_sc_defect_struct = _round_struct_coords(sorted_sc_defect_struct, to_unit_cell=True)

        return (
            (
                sorted_sc_defect_struct,
                sc_site,
                equiv_sites,
            )
            if return_sites
            else sorted_sc_defect_struct
        )

    def as_dict(self):
        """
        JSON-serializable dict representation of ``Defect``.

        Needs to be redefined because attributes not explicitly specified in
        subclasses, which is required for monty functions.
        """
        dict_wout_elt_changes = self.__dict__
        dict_wout_elt_changes.pop("_element_changes", None)  # not JSON serializable and unnecessary
        return {"@module": type(self).__module__, "@class": type(self).__name__, **dict_wout_elt_changes}

    def to_json(self, filename: PathLike | None = None):
        """
        Save the ``Defect`` object to a json file, which can be reloaded with
        the `` Defect``.from_json()`` class method.

        Note that file extensions with ".gz" will be automatically compressed
        (recommended to save space)!

        Args:
            filename (PathLike):
                Filename to save json file as. If None, the filename will
                be set as "{Defect.name}.json.gz".
        """
        if filename is None:
            filename = f"{self.name}.json.gz"

        dumpfn(self, filename)

    @classmethod
    def from_json(cls, filename: str):
        """
        Load a ``Defect`` object from a json(.gz) file.

        Note that ``.json.gz`` files can be loaded directly.

        Args:
            filename (PathLike):
                Filename of json file to load ``Defect`` from.

        Returns:
            ``Defect`` object
        """
        return loadfn(filename)

    def get_charge_states(self, padding: int = 1) -> list[int]:
        """
        Refactored version of ``pymatgen-analysis-defects``'s
        ``get_charge_states`` to not break when ``oxi_state`` is not set.
        """
        if self.user_charges:
            return self.user_charges

        if self.oxi_state is None or not isinstance(self.oxi_state, int | float):
            self._set_oxi_state()  # try guessing

        if self.oxi_state is None or not isinstance(self.oxi_state, int | float):  # still not set
            warnings.warn(
                f"Defect oxidation state not set and couldn't be guessed, returning charge"
                f"state range from -{padding} to +{padding}"
            )
            return [*range(-padding, padding + 1)]

        if isinstance(self.oxi_state, int) or self.oxi_state.is_integer():
            oxi_state = int(self.oxi_state)
        else:
            raise ValueError("Oxidation state must be an integer")

        if oxi_state >= 0:
            charges = [*range(-padding, oxi_state + padding + 1)]
        else:
            charges = [*range(oxi_state - padding, padding + 1)]

        return charges

    def get_multiplicity(
        self,
        primitive_structure: Structure | None = None,
        symprec: float | None = None,
        dist_tol_factor: float = 1.0,
        **kwargs,
    ) -> int:
        """
        Calculate the multiplicity of the defect site (``self.site``) in the
        host structure (``self.structure``).

        This function determines all equivalent sites of ``self.site`` in
        ``self.structure``, by first folding down to the primitive unit cell
        (which may be the same as ``self.structure``) and getting all
        equivalent primitive cell sites (which avoids issues with
        periodicity-breaking supercells, and boosts efficiency), then
        multiplying by ``len(self.structure)/len(primitive_structure)``, giving
        the site multiplicity in ``self.structure``.

        Args:
            primitive_structure (Structure | None):
                Structure to use for the primitive unit cell. Can be provided
                to avoid recalculation of the primitive cell.
            symprec (float):
                Symmetry precision to use for determining symmetry operations
                and thus equivalent sites with ``spglib``. Default is ``None``,
                which uses ``self.symprec`` (which is ``0.01`` by default,
                matching the ``pymatgen`` default. You may want to adjust
                for your system (e.g. if there are very slight octahedral
                distortions etc.). If ``fixed_symprec_and_dist_tol_factor`` is
                ``False`` (default), this value will be automatically adjusted
                (up to 10x, down to 0.1x) until the identified equivalent sites
                from ``spglib`` have consistent point group symmetries. Setting
                ``verbose`` to ``True`` will print information on the trialled
                ``symprec`` (and ``dist_tol_factor``) values.
            dist_tol_factor (float):
                Distance tolerance for clustering generated sites (to ensure
                they are truly distinct), as a multiplicative factor of
                ``symprec``. Default is 1.0 (i.e. ``dist_tol = symprec``, in
                Å). If ``fixed_symprec_and_dist_tol_factor`` is ``False``
                (default), this value will also be automatically adjusted if
                necessary (up to 10x, down to 0.1x)(after ``symprec``
                adjustments) until the identified equivalent sites from
                ``spglib`` have consistent point group symmetries. Setting
                ``verbose`` to ``True`` will print information on the trialled
                ``dist_tol_factor`` (and ``symprec``) values.
            **kwargs:
                Additional keyword arguments to pass to
                ``get_all_equiv_sites``, such as
                ``fixed_symprec_and_dist_tol_factor`` and ``verbose``.

        Returns:
            int: The multiplicity of ``self.site`` in ``self.structure``.
        """
        from doped.utils.symmetry import (
            get_all_equiv_sites,
            get_equiv_frac_coords_in_primitive,
            get_primitive_structure,
        )

        assert isinstance(self.structure, Structure)
        primitive_structure = primitive_structure or get_primitive_structure(
            self.structure,
            symprec=symprec or self.symprec,
        )
        if primitive_structure != self.structure:
            # accounts for potential periodicity breaking in Defect.structure (which may be a supercell):
            with contextlib.suppress(Exception):
                return len(
                    get_equiv_frac_coords_in_primitive(
                        self.site.frac_coords,
                        primitive_structure,
                        self.structure,
                        symprec=symprec or self.symprec,
                        dist_tol_factor=dist_tol_factor,
                        **kwargs,
                    )
                ) * round(len(self.structure) / len(primitive_structure))

        return len(
            get_all_equiv_sites(
                self.site.frac_coords,
                self.structure,
                just_frac_coords=True,
                symprec=symprec or self.symprec,
                dist_tol_factor=dist_tol_factor,
                **kwargs,
            )
        )

    def __setattr__(self, name, value):
        """
        Handle attribute updates.

        Safety function to ensure properties (``defect_site``, ``volume``,
        ``element_changes``) are recomputed whenever any defect attributes are
        changed, to ensure consistency and correct predictions.
        """
        super().__setattr__(name, value)
        if name in ["site", "structure"]:
            # delete internal pre-computed attributes, so they are re-computed when needed:
            for attr in ["_defect_site", "_volume", "_element_changes"]:
                if hasattr(self, attr):
                    delattr(self, attr)

    def __eq__(self, other) -> bool:
        """
        Determine whether two ``Defect`` objects are equal.

        Redefined from the parent method to be more robust (too loose ``stol``
        used in ``pymatgen-analysis-defects``) and much more efficient.
        """
        if not isinstance(other, type(self) | core.Defect):
            raise TypeError("Can only compare `Defect`s with `Defect`s!")

        if self.defect_type != other.defect_type:
            return False

        sm = StructureMatcher(stol=0.2, comparator=ElementComparator())

        return sm.fit(self.defect_structure, other.defect_structure)

    @property
    def defect_site(self) -> PeriodicSite:
        """
        The defect site in the structure.

        Re-written from ``pymatgen-analysis-defects`` version to be far more
        efficient, when used in loops (e.g. for calculating defect
        concentrations as functions of chemical potentials, temperature etc.).
        """
        if self.defect_type == core.DefectType.Interstitial:
            return self.site  # same as self.defect_site

        # else defect_site is the closest site in ``structure`` to the provided ``site``:
        if not hasattr(self, "_defect_site"):
            self._defect_site = min(
                self.structure.get_sites_in_sphere(
                    self.site.coords,
                    0.5,
                ),
                key=lambda x: x[1],
            )

        return self._defect_site

    @property
    def volume(self) -> float:
        """
        The volume (in Å³) of the structure in which the defect is created
        (i.e. ``Defect.structure``).

        Ensures volume is only computed once when calculating defect
        concentrations in loops (e.g. for calculating defect concentrations as
        functions of chemical potentials, temperature etc.).
        """
        if not hasattr(self, "_volume"):
            self._volume = self.structure.volume

        return self._volume

    @property
    def element_changes(self) -> dict[Element, int]:
        """
        The stoichiometry changes of the defect, as a dict.

        e.g. {"Mg": -1, "O": +1} for a O-on-Mg antisite in MgO. Redefined from
        the ``pymatgen-analysis-defects`` method to be far more efficient when
        used in loops (e.g. for calculating defect concentrations as functions
        of chemical potentials, temperature etc.).

        Returns:
            dict[Element, int]: The species changes of the defect.
        """
        if not hasattr(self, "_element_changes"):
            self._element_changes = super().element_changes

        return self._element_changes

    def __hash__(self):
        """
        Hash the ``Defect`` object, based on the defect name and site.
        """
        return hash((self.name, *tuple(np.round(self.site.frac_coords, 3))))


def remove_site_oxi_state(site: PeriodicSite):
    """
    Remove site oxidation state in-place.

    Same method as ``Structure.remove_oxidation_states()``,
    but applied to an individual site.

    Args:
        site (PeriodicSite):
            The site to remove oxidation states from.
    """
    new_sp: dict[Element, float] = collections.defaultdict(float)
    for el, occu in site.species.items():
        sym = el.symbol
        new_sp[Element(sym)] += occu
    site.species = Composition(new_sp)


def doped_defect_from_pmg_defect(
    defect: core.Defect, bulk_oxi_states: Structure | Composition | dict | bool = False, **doped_kwargs
):
    """
    Create the corresponding ``doped`` ``Defect`` (``Vacancy``,
    ``Interstitial``, ``Substitution``) from an input ``pymatgen`` ``Defect``
    object.

    Args:
        defect:
            ``pymatgen`` ``Defect`` object.
        bulk_oxi_states:
            Controls oxi-state guessing (later used for charge state guessing).
            By default, oxidation states are taken from
            ``doped_kwargs['oxi_state']`` if set, otherwise from
            ``bulk_oxi_states`` which can be either a ``pymatgen``
            ``Structure`` or ``Composition`` object, or a dict (of
            ``{element: oxi_state}``), or otherwise guessed using the ``doped``
            methods.
            If ``bulk_oxi_states`` is ``False``, then just uses the already-set
            ``Defect`` ``oxi_state`` attribute (default = 0), with no more
            guessing. If ``True``, re-guesses the oxidation state of the defect
            (ignoring the ``pymatgen`` ``Defect``  ``oxi_state``  attribute).

            If the structure is mixed-valence, then ``bulk_oxi_states``
            should be either a structure input or ``True`` (to re-guess).

            Default behaviour in ``doped`` generation is to provide
            ``bulk_oxi_states`` as an oxi-state decorated ``Structure``, to
            make defect setup more robust and efficient (particularly for odd
            input structures, such as defect supercells etc.). Oxidation states
            are removed from structures in the ``pymatgen`` defect generation
            functions, so this allows us to re-add them after.
        **doped_kwargs:
            Additional keyword arguments to define doped-specific attributes
            (see class docstring).
    """
    # determine defect type:
    if isinstance(defect, core.Vacancy):
        defect_type = Vacancy
    elif isinstance(defect, core.Substitution):
        defect_type = Substitution
    elif isinstance(defect, core.Interstitial):
        defect_type = Interstitial
    else:
        raise TypeError(
            f"Input defect must be a pymatgen Vacancy, Substitution or Interstitial object, "
            f"not {type(defect)}."
        )

    return defect_type._from_pmg_defect(defect, bulk_oxi_states=bulk_oxi_states, **doped_kwargs)


class Vacancy(Defect, core.Vacancy):
    def __init__(self, *args, **kwargs):
        """
        Subclass of ``pymatgen.analysis.defects.core.Vacancy`` with additional
        attributes and methods used by ``doped``.
        """
        super().__init__(*args, **kwargs)

    def __repr__(self) -> str:
        """
        String representation of a vacancy defect.
        """
        frac_coords_string = ",".join(f"{x:.3f}" for x in self.site.frac_coords)
        return f"{self.name} vacancy defect at site [{frac_coords_string}] in structure"


class Substitution(Defect, core.Substitution):
    def __init__(self, *args, **kwargs):
        """
        Subclass of ``pymatgen.analysis.defects.core.Substitution`` with
        additional attributes and methods used by ``doped``.
        """
        super().__init__(*args, **kwargs)

    def __repr__(self) -> str:
        """
        String representation of a substitutional defect.
        """
        frac_coords_string = ",".join(f"{x:.3f}" for x in self.site.frac_coords)
        return f"{self.name} substitution defect at site [{frac_coords_string}] in structure"


class Interstitial(Defect, core.Interstitial):
    def __init__(self, *args, **kwargs):
        """
        Subclass of ``pymatgen.analysis.defects.core.Interstitial`` with
        additional attributes and methods used by ``doped``.

        If ``multiplicity`` is not set in ``kwargs``, then it will be
        automatically calculated using ``get_multiplicity``. Keyword arguments
        for ``get_multiplicity``, such as ``symprec`` (-> ``self.symprec``),
        ``dist_tol_factor``, ``fixed_symprec_and_dist_tol_factor`` and
        ``verbose`` can also be passed in ``kwargs``.
        """
        calc_multiplicity = "multiplicity" not in kwargs
        kwargs.setdefault("multiplicity", 1)  # will break for Interstitials if not set
        multiplicity_kwargs = {
            k: kwargs.pop(k)
            for k in ["dist_tol_factor", "fixed_symprec_and_dist_tol_factor", "verbose"]
            if k in kwargs
        }  # symprec set as self.symprec and used by default in ``get_multiplicity``
        super().__init__(*args, **kwargs)
        if calc_multiplicity:
            self.multiplicity = self.get_multiplicity(**multiplicity_kwargs)

    def __repr__(self) -> str:
        """
        String representation of an interstitial defect.
        """
        frac_coords_string = ",".join(f"{x:.3f}" for x in self.site.frac_coords)
        return f"{self.name} interstitial defect at site [{frac_coords_string}] in structure"
