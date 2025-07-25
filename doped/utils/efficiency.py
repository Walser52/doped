"""
Utility functions to improve the efficiency of common
functions/workflows/calculations in ``doped``.
"""

import contextlib
import itertools
import operator
import re
from collections import defaultdict
from collections.abc import Callable, Generator, Sequence
from functools import cached_property, lru_cache
from typing import TYPE_CHECKING

import numpy as np
from numpy.typing import NDArray
from pymatgen.analysis.defects.generators import VacancyGenerator
from pymatgen.analysis.defects.utils import VoronoiPolyhedron, remove_collisions
from pymatgen.analysis.structure_matcher import (
    AbstractComparator,
    ElementComparator,
    FrameworkComparator,
    StructureMatcher,
)
from pymatgen.core.composition import Composition, DummySpecies
from pymatgen.core.periodic_table import Element, Species
from pymatgen.core.sites import PeriodicSite, Site
from pymatgen.core.structure import IStructure, Molecule, Structure
from pymatgen.io.vasp.sets import get_valid_magmom_struct
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer, SymmOp
from scipy.spatial import Voronoi

if TYPE_CHECKING:
    from doped.core import Vacancy

# Note that any overrides of ``__eq__`` should also override ``__hash__``, and vice versa


# Composition overrides:
def _composition__hash__(self):
    """
    Custom ``__hash__`` method for ``Composition`` instances, to make
    composition comparisons faster (used in structure matching etc.).

    ``pymatgen`` composition has just hashes the chemical system (without
    stoichiometry), which cannot then be used to distinguish different
    compositions.
    """
    return hash(frozenset(self._data.items()))


@lru_cache(maxsize=int(1e8))
def doped_Composition_eq_func(self_hash, other_hash):
    r"""
    Update equality function for ``Composition`` instances, which breaks early
    for mismatches and also uses caching, making it orders of magnitude faster
    than ``pymatgen``\s equality function.
    """
    self_comp = Composition.__instances__[self_hash]
    other_comp = Composition.__instances__[other_hash]

    return fast_Composition_eq(self_comp, other_comp)


def fast_Composition_eq(self, other):
    """
    Fast equality function for ``Composition`` instances, breaking early for
    mismatches.
    """
    # skip matching object type check here, as already checked upstream in ``_Composition__eq__``
    if len(self) != len(other):
        return False

    for el, amt in self.items():  # noqa: SIM110
        if abs(amt - other[el]) > type(self).amount_tolerance:
            return False

    return True


def _Composition__eq__(self, other):
    """
    Custom ``__eq__`` method for ``Composition`` instances, using a cached
    equality function to speed up comparisons.
    """
    if not isinstance(other, type(self) | dict):
        return NotImplemented

    # use object hash with instances to avoid recursion issues (for class method)
    self_hash = _composition__hash__(self)
    Composition.__instances__[self_hash] = self  # Ensure instances are stored for caching

    other_hash = _composition__hash__(other)
    Composition.__instances__[other_hash] = other

    return doped_Composition_eq_func(self_hash, other_hash)


class Hashabledict(dict):
    def __hash__(self):
        """
        Make the dictionary hashable by recursively "freezing" into only
        hashable built-ins, then hash that.

        Handles nested dicts, lists, sets, tuples, etc.
        """

        def _freeze(obj):
            if isinstance(obj, dict):  # convert to frozenset of tuples
                return frozenset((k, _freeze(v)) for k, v in obj.items())
            if isinstance(obj, list):  # lists → tuples
                return tuple(_freeze(v) for v in obj)
            if isinstance(obj, set):  # sets → frozensets
                return frozenset(_freeze(v) for v in obj)
            if isinstance(obj, tuple):  # tuples → tuples of frozen values
                return tuple(_freeze(v) for v in obj)
            # else assume it's already hashable (int, str, custom …)
            return obj

        return hash(_freeze(self))


def _get_hashable_dict(d: dict) -> Hashabledict:
    if isinstance(d, Hashabledict):
        return d
    if isinstance(d, dict):
        return Hashabledict(d)  # convert to hashable dict for caching purposes
    return d


def _fast_dict_deepcopy_max_two_levels(d: dict) -> dict:
    """
    Fast deepcopy of a dict with at most two levels of nested dicts (i.e. d →
    dict → dict → values).

    Implemented to allow fast deep-copying of nested chemical potential dicts,
    avoiding the overhead of `deepcopy` when looping over many chemical
    potential dicts.
    """
    return {
        k: (
            {
                k2: (v2.copy() if isinstance(v2, dict) else v2)  # final level, shallow copy sufficient
                for k2, v2 in v1.items()
            }
            if isinstance(v1, dict)
            else v1
        )
        for k, v1 in d.items()
    }


@lru_cache(maxsize=int(1e5))
def _cached_Composition_init(comp_input):
    return Composition(comp_input)


def _cache_ready_Composition_init(comp_input):
    return _cached_Composition_init(_get_hashable_dict(comp_input))


def _fast_get_composition_from_sites(sites, assume_full_occupancy=False):
    """
    Helper function to quickly get the composition of a collection of sites,
    faster than initializing a ``Structure`` object.

    Used in initial drafts of defect stenciling code, but replaced by faster
    methods.
    """
    elem_map: dict[Species, float] = defaultdict(float)
    for site in sites:
        if assume_full_occupancy:
            elem_map[next(iter(site._species))] += 1
        else:
            for species, occu in site.species.items():
                elem_map[species] += occu
    return Composition(elem_map)


Composition.__instances__ = {}
Composition.__eq__ = _Composition__eq__
Composition.__hash__ = _composition__hash__


@lru_cache(maxsize=int(1e5))
def _parse_site_species_str(site: Site, wout_charge: bool = False):
    if isinstance(site._species, Element):
        return site._species.symbol
    if isinstance(site._species, str):
        species_string = site._species
    elif isinstance(site._species, Composition | dict):
        species_string = str(next(iter(site._species)))
    else:
        raise ValueError(f"Unexpected species type: {type(site._species)}")

    if wout_charge:  # remove all digits, + or - from species string
        return re.sub(r"\d+|[\+\-]", "", species_string)
    return species_string


# PeriodicSite overrides:
def _periodic_site__hash__(self):
    """
    Custom ``__hash__`` method for ``PeriodicSite`` instances.
    """
    property_dict = (  # Convert properties to a hashable form
        {k: tuple(v) if isinstance(v, list | np.ndarray) else v for k, v in self.properties.items()}
        if self.properties
        else {}
    )

    species_info = tuple(str(el) for el in self.species)  # string representation is used for species hash
    try:
        return hash(
            (
                species_info,
                tuple(self.coords),
                frozenset(property_dict.items()),
            )
        )
    except Exception:  # hash without the property dict
        return hash(
            (
                species_info,
                tuple(self.coords),
            )
        )


def cache_ready_PeriodicSite__eq__(self, other):
    """
    Custom ``__eq__`` method for ``PeriodicSite`` instances, using a cached
    equality function to speed up comparisons.
    """
    needed_attrs = ("_species", "coords", "properties")

    if not all(hasattr(other, attr) for attr in needed_attrs):
        return NotImplemented

    return (
        self._species == other._species  # should always work fine (and is faster) if Site initialised
        # without ``skip_checks`` (default)
        and cached_allclose(tuple(self.coords), tuple(other.coords), atol=type(self).position_atol)
        and self.properties == other.properties
    )


@lru_cache(maxsize=int(1e8))
def cached_allclose(a: tuple, b: tuple, rtol: float = 1e-05, atol: float = 1e-08):
    """
    Cached version of ``np.allclose``, taking tuples as inputs (so that they
    are hashable and thus cacheable).
    """
    return np.allclose(np.array(a), np.array(b), rtol=rtol, atol=atol)


PeriodicSite.__eq__ = cache_ready_PeriodicSite__eq__
PeriodicSite.__hash__ = _periodic_site__hash__

# Structure overrides:


def _structure__hash__(self):
    """
    Custom ``__hash__`` method for ``Structure`` instances.
    """
    return hash((self.lattice, frozenset(self.sites)))


@contextlib.contextmanager
def cache_species(structure_cls):
    """
    Context manager that makes ``Structure.species`` a cached property, which
    significantly speeds up ``pydefect`` eigenvalue parsing in large structures
    (due to repeated use of ``Structure.indices_from_symbol``.
    """
    Composition.__eq__ = _Composition__eq__
    Composition.__hash__ = _composition__hash__  # use efficient hash for composition
    original_species = structure_cls.species
    try:
        cached = cached_property(original_species.fget)
        cached.__set_name__(structure_cls, "species")  # Explicit initialization
        structure_cls.species = cached
        yield
    finally:
        structure_cls.species = original_species


def doped_Structure__eq__(self, other: IStructure) -> bool:
    """
    Copied from ``pymatgen``, but updated to break early once a mis-matching
    site is found, to speed up structure matching by ~2x.
    """
    # skip matching object type check here, as already checked upstream in ``_Structure__eq__``
    if other is self:
        return True
    if len(self) != len(other):
        return False
    if self.lattice != other.lattice:
        return False
    if self.properties != other.properties:
        return False
    for site in self:  # noqa: SIM110
        if site not in other:
            return False  # break early!
    return True


@lru_cache(maxsize=int(1e4))
def cached_Structure_eq_func(self_hash, other_hash):
    """
    Cached equality function for ``Structure`` instances.
    """
    return doped_Structure__eq__(IStructure.__instances__[self_hash], IStructure.__instances__[other_hash])


def _Structure__eq__(self, other):
    """
    Custom ``__eq__`` method for ``Structure``/``IStructure`` instances, using
    both caching and an updated, faster equality function to speed up
    comparisons.
    """
    needed_attrs = ("lattice", "sites", "properties")

    if not all(hasattr(other, attr) for attr in needed_attrs):
        return NotImplemented

    self_hash = _structure__hash__(self)
    other_hash = _structure__hash__(other)

    IStructure.__instances__[self_hash] = self  # Ensure instances are stored for caching
    IStructure.__instances__[other_hash] = other

    return cached_Structure_eq_func(self_hash, other_hash)


IStructure.__eq__ = _Structure__eq__
IStructure.__hash__ = _structure__hash__
IStructure.__instances__ = {}
Structure.__eq__ = _Structure__eq__
Structure.__hash__ = _structure__hash__
Structure.__deepcopy__ = lambda x, y: x.copy()  # make deepcopying faster, shallow copy fine for structures


# Molecule overrides:
def _DopedMolecule__hash__(self):
    """
    Hash ``pymatgen`` ``Molecule`` objects using the z-matrix (which reflects
    the lengths, angles, and atom types of the molecule) and the site
    coordinates.

    Implemented to allow caching for efficient determination of symmetry
    equivalent ``Molecule`` objects. The z-matrix functions as a unique
    identifier for a molecule (with translation/rotation invariance -- which is
    then removed by including the actual coordinates), while the ``__hash__``
    method for the parent ``Molecule`` class is based solely on the composition
    of the molecule and thus not unique.
    """
    z_list = self.get_zmatrix().split("\n")
    rounded_z_list = tuple([round(float(i.split("=")[-1]), 2) if "=" in i else i for i in z_list])
    return hash((rounded_z_list, tuple(tuple(np.round(site.coords, 3)) for site in self)))


def _DopedMolecule__eq__(self, other):
    """
    Custom ``__eq__`` method for ``Molecule`` instances, using a cached
    equality function to speed up comparisons.
    """
    if not isinstance(other, type(self)):
        return NotImplemented
    return _DopedMolecule__hash__(self) == _DopedMolecule__hash__(other)


Molecule.__eq__ = _DopedMolecule__eq__
Molecule.__hash__ = _DopedMolecule__hash__


# SpacegroupAnalyzer overrides:
def _sga__hash__(self):
    """
    Custom ``__hash__`` method for ``SpacegroupAnalyzer`` instances, to make
    them hashable for efficient caching of e.g. symmetry operation generation.
    """
    return hash((self._cell, self._symprec, self._angle_tol))


_original_get_symmetry = SpacegroupAnalyzer._get_symmetry


@lru_cache(maxsize=int(1e3))
def _get_symmetry(self) -> tuple[NDArray, NDArray]:
    """
    Get the symmetry operations associated with the structure.

    Refactored from ``pymatgen`` to allow caching, to boost efficiency when
    working with large defect supercells.
    """
    return _original_get_symmetry(self)  # call the original method, with the now cacheable class


_original_get_symmetry_operations = SpacegroupAnalyzer.get_symmetry_operations


@lru_cache(maxsize=int(1e3))
def _get_symmetry_operations(self, cartesian: bool = False) -> list[SymmOp]:
    """
    Get the symmetry operations associated with the structure.

    Refactored from ``pymatgen`` to allow caching, to boost efficiency.
    """
    return _original_get_symmetry_operations(self)  # call the original method, now a cacheable class


SpacegroupAnalyzer.__hash__ = _sga__hash__
SpacegroupAnalyzer._get_symmetry = _get_symmetry
SpacegroupAnalyzer.get_symmetry_operations = _get_symmetry_operations


def _get_symbol(element: Element | Species, comparator: AbstractComparator | None = None) -> str:
    """
    Convenience function to get the symbol of an ``Element`` or ``Species`` as
    a string, with charge information included or excluded depending on the
    choice of ``comparator``.

    By default, the returned symbol does not include any charge / oxidation
    state information. If ``comparator`` is provided and is not
    ``ElementComparator`` / ``FrameworkComparator``, then the ``str(element)``
    representation is returned (which will include charge information if
    ``element`` is a ``Species``).

    Args:
        element (Element | Species):
            ``Element`` or ``Species`` to get the symbol of.
        comparator (AbstractComparator | None):
            Comparator to check if we should return the ``str(element)``
            representation (which includes charge information if ``element`` is
            a ``Species``), or just the element symbol (i.e.
            ``element.symbol``, or ``element.element.symbol`` if ``element`` is
            a ``Species`` object) -- which is the case when ``comparator`` is
            ``None`` (default) or ``ElementComparator`` /
            ``FrameworkComparator``.

    Returns:
        str: Symbol of the element as a string.
    """
    if (
        comparator is not None
        and not isinstance(comparator, ElementComparator | FrameworkComparator)
        and isinstance(element, Species)
    ):
        return str(element)
    return element.symbol if isinstance(element, Element | DummySpecies) else element.element.symbol


def get_element_indices(
    structure: Structure,
    elements: list[Element | Species | str] | None = None,
    comparator: AbstractComparator | None = None,
) -> dict[str, list[int]]:
    """
    Convenience function to generate a dictionary of ``{element: [indices]}``
    for a given ``Structure``, where ``indices`` are the indices of the sites
    in the structure corresponding to the given ``elements`` (default is all
    elements in the structure).

    Args:
        structure (Structure):
            ``Structure`` to get the indices from.
        elements (list[Element | Species | str] | None):
            List of elements to get the indices of. If ``None`` (default), all
            elements in the structure are used.
        comparator (AbstractComparator | None):
            Comparator to check if we should return the ``str(element)``
            representation (which includes charge information if ``element`` is
            a ``Species``), or just the element symbol (i.e.
            ``element.element.symbol``) -- which is the case when
            ``comparator`` is ``None`` (default) or ``ElementComparator`` /
            ``FrameworkComparator``.

    Returns:
        dict[str, list[int]]:
            Dictionary of ``{element: [indices]}`` for the given ``elements``
            in the structure.
    """
    if elements is None:
        elements = _fast_get_composition_from_sites(structure).elements

    if not all(isinstance(element, str) for element in elements):
        elements = [_get_symbol(element, comparator) for element in elements]
    species = np.array([_get_symbol(site.specie, comparator) for site in structure])
    return {element: np.where(species == element)[0].tolist() for element in elements}


def get_element_min_max_bond_length_dict(structure: Structure, **sm_kwargs) -> dict:
    r"""
    Get a dictionary of ``{element: (min_bond_length, max_bond_length)}`` for a
    given ``Structure``, where ``min_bond_length`` and ``max_bond_length`` are
    the minimum and maximum bond lengths for each element in the structure.

    Args:
        structure (Structure):
            Structure to calculate bond lengths for.
        **sm_kwargs:
            Additional keyword arguments to pass to ``StructureMatcher()``.
            Just used to check if ``comparator`` has been set here (if
            ``ElementComparator``/``FrameworkComparator`` used, then we use
            ``Element``\s rather than ``Species`` as the keys), or if
            ``ignored_species`` is set (in which case these species are
            ignored when calculating bond lengths).

    Returns:
        dict: Dictionary of ``{element: (min_bond_length, max_bond_length)}``.
    """
    comparator = sm_kwargs.get("comparator")

    if len(structure) == 1:
        structure = structure * 2  # need at least two sites to calculate bond lengths

    # get the distance matrix broken down by species:
    element_idx_dict = get_element_indices(structure, comparator=comparator)
    ignored_indices = [
        idx for elt in sm_kwargs.get("ignored_species", []) for idx in element_idx_dict.get(elt, [])
    ]

    distance_matrix = structure.distance_matrix
    np.fill_diagonal(distance_matrix, np.inf)  # set diagonal to np.inf to ignore self-distances of 0
    distance_matrix[:, ignored_indices] = np.inf  # set ignored indices to np.inf to ignore these distances
    distance_matrix[ignored_indices, :] = np.inf  # set ignored indices to np.inf to ignore these distances
    element_min_max_bond_length_dict = {elt: np.array([0, 0]) for elt in element_idx_dict}

    for elt, site_indices in element_idx_dict.items():
        element_dist_matrix = distance_matrix[:, site_indices]  # (N_of_that_element, N_sites) matrix
        if element_dist_matrix.size != 0:
            min_interatomic_distances_per_atom = np.min(element_dist_matrix, axis=0)  # min along columns
            element_min_max_bond_length_dict[elt] = np.array(
                [np.min(min_interatomic_distances_per_atom), np.max(min_interatomic_distances_per_atom)]
            )

    return element_min_max_bond_length_dict


def get_dist_equiv_stol(dist: float, structure: Structure) -> float:
    """
    Get the equivalent ``stol`` value for a given Cartesian distance (``dist``)
    in a given ``Structure``.

    ``stol`` is a site tolerance parameter used in ``pymatgen``
    ``StructureMatcher`` functions, defined as the fraction of the average free
    length per atom := ( V / Nsites ) ** (1/3).

    Args:
        dist (float): Cartesian distance in Å.
        structure (Structure): Structure to calculate ``stol`` for.

    Returns:
        float: Equivalent ``stol`` value for the given distance.
    """
    return dist / (structure.volume / len(structure)) ** (1 / 3)


def get_min_stol_for_s1_s2(struct1: Structure, struct2: Structure, **sm_kwargs) -> float:
    """
    Get the minimum possible ``stol`` value which will give a match between
    ``struct1`` and ``struct2`` using ``StructureMatcher``, based on the ranges
    of per-element minimum interatomic distances in the two structures.

    Args:
        struct1 (Structure): Initial structure.
        struct2 (Structure): Final structure.
        **sm_kwargs:
            Additional keyword arguments to pass to ``StructureMatcher()``.
            Just used to check if ``ignored_species`` or ``comparator`` has
            been set here.

    Returns:
        float:
            Minimum ``stol`` value for a match between ``struct1`` and
            ``struct2``. If a direct match is detected (corresponding to min
            ``stol`` = 0, then ``1e-4`` is returned).
    """
    s1_min_max_bond_length_dict = get_element_min_max_bond_length_dict(struct1, **sm_kwargs)
    s2_min_max_bond_length_dict = get_element_min_max_bond_length_dict(struct2, **sm_kwargs)
    common_elts = set(s1_min_max_bond_length_dict.keys()) & set(s2_min_max_bond_length_dict.keys())
    if not common_elts:  # try without oxidation states
        struct1_wout_oxi = struct1.copy()
        struct2_wout_oxi = struct2.copy()
        struct1_wout_oxi.remove_oxidation_states()
        struct2_wout_oxi.remove_oxidation_states()
        s1_min_max_bond_length_dict = get_element_min_max_bond_length_dict(struct1_wout_oxi, **sm_kwargs)
        s2_min_max_bond_length_dict = get_element_min_max_bond_length_dict(struct2_wout_oxi, **sm_kwargs)
        common_elts = set(s1_min_max_bond_length_dict.keys()) & set(s2_min_max_bond_length_dict.keys())

    min_min_dist_change = 1e-4
    with contextlib.suppress(Exception):
        min_min_dist_change = max(
            {
                elt: max(np.abs(s1_min_max_bond_length_dict[elt] - s2_min_max_bond_length_dict[elt]))
                for elt in common_elts
                if elt not in sm_kwargs.get("ignored_species", [])
            }.values()
        )

    return max(get_dist_equiv_stol(min_min_dist_change, struct1), 1e-4)


def _sm_get_atomic_disps(sm: StructureMatcher, struct1: Structure, struct2: Structure):
    """
    Get the root-mean-square displacement `and atomic displacements` between
    two structures, normalized by the mean free length per atom:
    ``(Vol/Nsites)^(1/3)``.

    These values are not directly returned by ``StructureMatcher`` methods.
    This function replicates ``StructureMatcher.get_rms_dist()``, but changes
    the return value from ``match[0], max(match[1])`` to ``match[0], match[1]``
    to allow further analysis of displacements. Mainly intended for use by
    ``ShakeNBreak``.

    Args:
        sm (StructureMatcher): ``pymatgen`` ``StructureMatcher`` object.
        struct1 (Structure): Initial structure.
        struct2 (Structure): Final structure.

    Returns:
        tuple:

            - float: Normalised RMS displacement between the two structures.
            - np.ndarray: Normalised displacements between the two structures.

        or ``None`` if no match is found.
    """
    struct1, struct2 = sm._process_species([struct1, struct2])
    struct1, struct2, fu, s1_supercell = sm._preprocess(struct1, struct2)
    match = sm._match(struct1, struct2, fu, s1_supercell, use_rms=True, break_on_match=False)

    return None if match is None else (match[0], match[1])


def StructureMatcher_scan_stol(
    struct1: Structure,
    struct2: Structure,
    func_name: str = "get_s2_like_s1",
    min_stol: float | None = None,
    max_stol: float = 5.0,
    stol_factor: float = 0.5,
    **sm_kwargs,
):
    r"""
    Utility function to scan through a range of ``stol`` values for
    ``StructureMatcher`` until a match is found between ``struct1`` and
    ``struct2`` (i.e. ``StructureMatcher.{func_name}`` returns a result).

    The ``StructureMatcher.match()`` function (used in most
    ``StructureMatcher`` methods) speed is heavily dependent on ``stol``, with
    smaller values being faster, so we can speed up evaluation by starting with
    small values and increasing until a match is found (especially with the
    ``doped`` efficiency tools which implement caching (and other improvements)
    to ensure no redundant work here).

    Note that ``ElementComparator()`` is used by default here! (So sites with
    different species but the same element (e.g. "S2-" & "S0+") will be
    considered match-able). This can be controlled with
    ``sm_kwargs['comparator']``.

    Args:
        struct1 (Structure): ``struct1`` for ``StructureMatcher.match()``.
        struct2 (Structure): ``struct2`` for ``StructureMatcher.match()``.
        func_name (str):
            The name of the ``StructureMatcher`` method to return the result
            of ``StructureMatcher.{func_name}(struct1, struct2)`` for, such
            as:

            - "get_s2_like_s1" (default)
            - "get_rms_dist"
            - "fit"
            - "fit_anonymous"
        min_stol (float):
            Minimum ``stol`` value to try. Default is to use ``doped``\s
            ``get_min_stol_for_s1_s2()`` function to estimate the minimum
            ``stol`` necessary, and start with 2x this value to achieve fast
            structure-matching in most cases.
        max_stol (float):
            Maximum ``stol`` value to try. Default: 5.0.
        stol_factor (float):
            Fractional increment to increase ``stol`` by each time (when a
            match is not found). Default value of 0.5 increases ``stol`` by 50%
            each time.
        **sm_kwargs:
            Additional keyword arguments to pass to ``StructureMatcher()``.

    Returns:
        Result of ``StructureMatcher.{func_name}(struct1, struct2)`` or
        ``None`` if no match is found.
    """
    # use doped efficiency tools to make structure-matching as fast as possible:
    StructureMatcher._get_atomic_disps = _sm_get_atomic_disps  # monkey-patch ``StructureMatcher`` for SnB

    if "comparator" not in sm_kwargs:
        sm_kwargs["comparator"] = ElementComparator()

    if min_stol is None:
        min_stol = get_min_stol_for_s1_s2(struct1, struct2, **sm_kwargs) * 2

    # here we cycle through a range of stols, because we just need to find the closest match so we could
    # use a high ``stol`` from the start and it would give correct result, but higher ``stol``\s take
    # much longer to run as it cycles through multiple possible matches. So we start with a low ``stol``
    # and break once a match is found:
    stol = min_stol
    while stol < max_stol:
        if user_stol := sm_kwargs.pop("stol", False):  # first run, try using user-provided stol first:
            sm_full_user_custom = StructureMatcher(stol=user_stol, **sm_kwargs)
            result = getattr(sm_full_user_custom, func_name)(struct1, struct2)
            if result is not None:
                return result

        sm = StructureMatcher(stol=stol, **sm_kwargs)
        result = getattr(sm, func_name)(struct1, struct2)
        if result is not None:
            return result

        stol *= 1 + stol_factor
        # Note: this function could possibly be sped up if ``StructureMatcher._match()`` was updated to
        # return the guessed ``best_match`` value (even if larger than ``stol``), which will always be
        # >= the best possible match it seems, and then using this to determine the next ``stol`` value
        # to trial. Seems like it could give a ~50% speedup in some cases? Not clear though,
        # as once you're getting a reasonable guessed value out, the trial ``stol`` should be pretty
        # close to the necessary value anyway.

    return None


StructureMatcher._get_atomic_disps = _sm_get_atomic_disps  # monkey-patch ``StructureMatcher`` for SnB


class DopedTopographyAnalyzer:
    """
    This is a modified version of
    ``pymatgen.analysis.defects.utils.TopographyAnalyzer`` to lean down the
    input options and make initialisation far more efficient (~2 orders of
    magnitude faster).

    The original code was written by Danny Broberg and colleagues
    (10.1016/j.cpc.2018.01.004), which was then added to ``pymatgen`` before
    being cut.
    """

    def __init__(
        self,
        structure: Structure,
        image_tol: float = 0.0001,
        max_cell_range: int = 1,
        constrained_c_frac: float = 0.5,
        thickness: float = 0.5,
    ) -> None:
        """
        Args:
            structure (Structure):
                Structure to analyse.
            image_tol (float):
                A tolerance distance for the analysis, used to determine if
                sites are periodic images of each other. Default (of 1e-4) is
                usually fine.
            max_cell_range (int):
                This is the range of periodic images to construct the Voronoi
                tessellation. A value of 1 means that we include all points
                from ``(x +- 1, y +- 1, z+- 1)`` in the Voronoi construction.
                This is because the Voronoi polyhedra extend beyond the
                standard unit cell because of PBC. Typically, the default value
                of 1 works fine for most structures and is fast. But for very
                small unit cells with high symmetry, this may need to be
                increased to 2 or higher. If there are < 5 atoms in the input
                structure and ``max_cell_range`` is 1, this will automatically
                be increased to 2.
            constrained_c_frac (float):
                Constrain the region where topology analysis is performed.
                Only sites with ``z`` fractional coordinates between
                ``constrained_c_frac +/- thickness`` are considered. Default of
                0.5 (with ``thickness`` of 0.5) includes all sites in the unit
                cell.
            thickness (float):
                Constrain the region where topology analysis is performed.
                Only sites with ``z`` fractional coordinates between
                ``constrained_c_frac +/- thickness`` are considered. Default of
                0.5 (with ``thickness`` of 0.5) includes all sites in the unit
                cell.
        """
        # if input cell is very small (< 5 atoms) and max cell range is 1 (default), bump to 2 for
        # accurate Voronoi tessellation:
        if len(structure) < 5 and max_cell_range == 1:
            max_cell_range = 2

        self.structure = structure.copy()
        self.structure.remove_oxidation_states()

        constrained_sites = []
        for _i, site in enumerate(self.structure):
            if (
                site.frac_coords[2] >= constrained_c_frac - thickness
                and site.frac_coords[2] <= constrained_c_frac + thickness
            ):
                constrained_sites.append(site)
        constrained_struct = Structure.from_sites(sites=constrained_sites)
        lattice = constrained_struct.lattice

        coords = []
        cell_range = list(range(-max_cell_range, max_cell_range + 1))
        for shift in itertools.product(cell_range, cell_range, cell_range):
            for site in constrained_struct.sites:
                shifted = site.frac_coords + shift
                coords.append(lattice.get_cartesian_coords(shifted))

        # Perform the voronoi tessellation.
        voro = Voronoi(coords)
        node_points_map = defaultdict(set)
        for pts, vs in voro.ridge_dict.items():
            for v in vs:
                node_points_map[v].update(pts)

        vnodes: list[VoronoiPolyhedron] = []

        def get_mapping(vnodes, poly: VoronoiPolyhedron):
            """
            Check if a Voronoi Polyhedron is a periodic image of one of the
            existing polyhedra.

            Modified to avoid expensive ``np.allclose()`` calls.
            """
            if not vnodes:
                return None
            distance_matrix = lattice.get_all_distances([v.frac_coords for v in vnodes], poly.frac_coords)
            if np.any(distance_matrix < image_tol):
                for v in vnodes:
                    if v.is_image(poly, image_tol):
                        return v
            return None

        # Filter all the voronoi polyhedra so that we only consider those
        # which are within the unit cell:
        for i, vertex in enumerate(voro.vertices):
            if i == 0:
                continue
            fcoord = lattice.get_fractional_coords(vertex)
            if np.all([-image_tol <= c < 1 + image_tol for c in fcoord]):
                poly = VoronoiPolyhedron(lattice, fcoord, node_points_map[i], coords, i)
                if get_mapping(vnodes, poly) is None:
                    vnodes.append(poly)

        self.coords = coords
        self.vnodes = vnodes


def get_voronoi_nodes(structure: Structure) -> list[PeriodicSite]:
    """
    Get the Voronoi nodes of a ``pymatgen`` ``Structure``.

    Maximises efficiency by mapping down to the primitive cell, doing Voronoi
    analysis (with the efficient ``DopedTopographyAnalyzer`` class), and then
    mapping back to the original structure (typically a supercell).

    Args:
        structure (Structure):
            ``pymatgen`` ``Structure`` object.

    Returns:
        list[PeriodicSite]:
            List of ``PeriodicSite`` objects representing the Voronoi nodes.
    """
    try:
        return _hashable_get_voronoi_nodes(structure)
    except TypeError:
        structure.__hash__ = _structure__hash__  # make sure Structure is hashable
        return _hashable_get_voronoi_nodes(structure)


@lru_cache(maxsize=int(1e2))
def _hashable_get_voronoi_nodes(structure: Structure) -> list[PeriodicSite]:
    from doped.utils.symmetry import _doped_cluster_frac_coords, get_primitive_structure

    # map all sites to the unit cell; 0 ≤ xyz < 1.
    structure = Structure.from_sites(structure, to_unit_cell=True)
    # get Voronoi nodes in primitive structure and then map back to the supercell:
    prim_structure = get_primitive_structure(structure)

    top_analyzer = DopedTopographyAnalyzer(prim_structure)
    voronoi_coords = [v.frac_coords for v in top_analyzer.vnodes]
    # remove nodes less than 0.5 Å from sites in the structure
    voronoi_coords = remove_collisions(voronoi_coords, structure=prim_structure, min_dist=0.5)
    # cluster nodes within 0.2 Å of each other:
    prim_vnodes: np.ndarray = _doped_cluster_frac_coords(voronoi_coords, prim_structure, tol=0.2)

    # map back to the supercell
    sm = StructureMatcher(primitive_cell=False, attempt_supercell=True)
    mapping = sm.get_supercell_matrix(structure, prim_structure)
    voronoi_struct = Structure.from_sites(
        [PeriodicSite("X", fpos, structure.lattice) for fpos in prim_vnodes]
    )  # Structure with Voronoi nodes as sites
    voronoi_struct.make_supercell(mapping)  # Map back to the supercell

    # check if there was an origin shift between primitive and supercell
    regenerated_supercell = prim_structure.copy()
    regenerated_supercell.make_supercell(mapping)
    fractional_shift = sm.get_transformation(structure, regenerated_supercell)[1]
    if not np.allclose(fractional_shift, 0):
        voronoi_struct.translate_sites(range(len(voronoi_struct)), fractional_shift, frac_coords=True)

    return voronoi_struct.sites


def _generic_group_labels(list_in: Sequence, comp: Callable = operator.eq) -> list[int]:
    """
    Group a list of unsortable objects, using a given comparator function.

    Templated off the ``pymatgen-analysis-defects`` function, but fixed to
    avoid broken reassignment logic and overwriting of labels (resulting in
    sites being incorrectly dropped).

    Previously in ``doped`` interstitial generation, but then removed after
    updates in commit ``4699f38`` (for v3.0.0) to use faster site-matching
    functions from ``doped``.

    Args:
        list_in (Sequence): A sequence of objects to group using ``comp``.
        comp (Callable): A comparator function.

    Returns:
        list[int]: list of labels for the input list
    """
    list_out = [-1] * len(list_in)  # Initialize with -1 instead of None for clarity
    label_num = 0

    for i1 in range(len(list_in)):
        if list_out[i1] != -1:  # Already labeled
            continue
        list_out[i1] = label_num
        for i2 in range(i1 + 1, len(list_in)):
            if list_out[i2] == -1 and comp(list_in[i1], list_in[i2]):
                list_out[i2] = label_num
        label_num += 1

    return list_out


class DopedVacancyGenerator(VacancyGenerator):
    """
    Vacancy defects generator, subclassed from ``pymatgen-analysis-defects`` to
    improve efficiency (particularly when handling defect complexes).
    """

    def generate(
        self,
        structure: Structure,
        rm_species: set[str | Species] | list[str | Species] | None = None,
        **kwargs,
    ) -> Generator["Vacancy", None, None]:
        """
        Generate vacancy defects.

        Args:
            structure (Structure):
                The structure to generate vacancy defects in.
            rm_species (set[str | Species] | list[str | Species] | None):
                List/set of species to be removed (i.e. to consider for vacancy
                generation). If ``None``, considers all species.
            **kwargs:
                Additional keyword arguments for the ``Vacancy`` constructor.

        Returns:
            Generator[Vacancy, None, None]:
                Generator that yields a list of ``Vacancy`` objects.
        """
        from doped.core import Vacancy
        from doped.utils.symmetry import get_sga

        # core difference is the removal of unnecessary `remove_oxidation_states` calls
        structure = get_valid_magmom_struct(structure)
        all_species = {elt.symbol for elt in structure.composition.elements}
        rm_species = all_species if rm_species is None else {*map(str, rm_species)}

        if not set(rm_species).issubset(all_species):
            raise ValueError(
                f"rm_species ({rm_species}) must be a subset of the structure's species ({all_species})."
            )

        sga = get_sga(structure)
        sym_struct = sga.get_symmetrized_structure()
        for site_group in sym_struct.equivalent_sites:
            site = site_group[0]
            if site.specie.symbol in rm_species:
                yield Vacancy(
                    structure=structure,  # note that we no longer remove oxi states here! or in get_sga
                    site=site,
                    equivalent_sites=site_group,
                    **kwargs,
                )
