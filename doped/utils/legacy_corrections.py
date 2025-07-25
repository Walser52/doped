"""
Functions for computing legacy finite-size charge corrections (Makov-Payne,
Murphy-Hine, Lany-Zunger) for defect formation energies.

Mostly adapted from the deprecated AIDE package developed by the dynamic duo
Adam Jackson and Alex Ganose.
"""

import copy
import itertools
from math import erfc, exp

import numpy as np

from doped.utils.parsing import _get_bulk_supercell


def get_murphy_image_charge_correction(
    lattice,
    dielectric_matrix,
    conv=0.3,
    factor=30,
    verbose=False,
):
    """
    Calculates the anisotropic image charge correction by Sam Murphy in eV.

    This a rewrite of the code 'madelung.pl' written by Sam Murphy (see [1]).
    The default convergence parameter of conv = 0.3 seems to work perfectly
    well. However, it may be worth testing convergence of defect energies with
    respect to the factor (i.e. cut-off radius).

    Reference: S. T. Murphy and N. D. H. Hine, Phys. Rev. B 87, 094111 (2013).

    Args:
        lattice (list):
            The defect cell lattice as a 3x3 matrix.
        dielectric_matrix (list):
            The dielectric tensor as 3x3 matrix.
        conv (float):
            A value between 0.1 and 0.9 which adjusts how much real space vs
            reciprocal space contribution there is.
        factor:
            The cut-off radius, defined as a multiple of the longest cell
            parameter.
        verbose (bool):
            If True details of the correction will be printed.

    Returns:
        The image charge correction as a ``{charge: correction}`` dictionary.
    """
    inv_diel = np.linalg.inv(dielectric_matrix)
    det_diel = np.linalg.det(dielectric_matrix)
    latt = np.sqrt(np.sum(lattice**2, axis=1))

    # calc real space cutoff
    longest = max(latt)
    r_c = factor * longest

    # Estimate the number of boxes required in each direction to ensure
    # r_c is contained (the tens are added to ensure the number of cells
    # contains r_c). This defines the size of the supercell in which
    # the real space section is performed, however only atoms within rc
    # will be conunted.
    axis = np.array([int(r_c / a + 10) for a in latt])

    # Calculate supercell parallelepiped and dimensions
    sup_latt = np.dot(np.diag(axis), lattice)

    # Determine which of the lattice calculation_metadata is the largest and determine
    # reciprocal space supercell
    recip_axis = np.array([int(x) for x in factor * max(latt) / latt])
    recip_volume = abs(np.dot(np.cross(lattice[0], lattice[1]), lattice[2]))

    # Calculatate the reciprocal lattice vectors (need factor of 2 pi)
    recip_latt = np.linalg.inv(lattice).T * 2 * np.pi

    real_space = _get_real_space(conv, inv_diel, det_diel, r_c, axis, sup_latt)
    reciprocal = _get_recip(
        conv,
        recip_axis,
        recip_volume,
        recip_latt,
        dielectric_matrix,
    )

    # calculate the other terms and the final Madelung potential
    third_term = -2 * conv / np.sqrt(np.pi * det_diel)
    fourth_term = -3.141592654 / (recip_volume * conv**2)
    madelung = -(real_space + reciprocal + third_term + fourth_term)

    # convert to atomic units
    conversion = 14.39942
    real_ev = real_space * conversion / 2
    recip_ev = reciprocal * conversion / 2
    third_ev = third_term * conversion / 2
    fourth_ev = fourth_term * conversion / 2
    madelung_ev = madelung * conversion / 2

    correction = {}
    for q in range(1, 8):
        makov = 0.5 * madelung * q**2 * conversion
        lany = 0.65 * makov
        correction[q] = makov

    if verbose:
        print(
            f"""
    Results                      v_M^scr    dE(q=1) /eV
    -----------------------------------------------------
    Real space contribution    =  {real_space:.6f}     {real_ev:.6f}
    Reciprocal space component =  {reciprocal:.6f}     {recip_ev:.6f}
    Third term                 = {third_term:.6f}    {third_ev:.6f}
    Neutralising background    = {fourth_term:.6f}    {fourth_ev:.6f}
    -----------------------------------------------------
    Final Madelung potential   = {madelung:.6f}     {madelung_ev:.6f}
    -----------------------------------------------------"""
        )

        print(
            """
    Here are your final corrections:
    +--------+------------------+-----------------+
    | Charge | Point charge /eV | Lany-Zunger /eV |
    +--------+------------------+-----------------+"""
        )
        for q in range(1, 8):
            makov = 0.5 * madelung * q**2 * conversion
            lany = 0.65 * makov
            correction[q] = makov
            print(f"|   {q}    |     {makov:10f}   |    {lany:10f}   |")
        print("+--------+------------------+-----------------+")

    return correction


def _get_real_space(conv, inv_diel, det_diel, r_c, axis, sup_latt):
    # Calculate real space component
    axis_ranges = [range(-a, a) for a in axis]

    # Pre-compute square of cutoff distance for cheaper comparison than
    # separation < r_c
    r_c_sq = r_c**2

    def _real_loop_function(mno):
        # Calculate the defect's fractional position in extended supercell
        d_super = np.array(mno, dtype=float) / axis
        d_super_cart = np.dot(d_super, sup_latt)

        # Test if the new atom coordinates fall within r_c, then solve
        separation_sq = np.sum(np.square(d_super_cart))
        # Take all cases within r_c except m,n,o != 0,0,0
        if separation_sq < r_c_sq and any(mno):
            mod = np.dot(d_super_cart, inv_diel)
            dot_prod = np.dot(mod, d_super_cart)
            N = np.sqrt(dot_prod)
            return 1 / np.sqrt(det_diel) * erfc(conv * N) / N

        return 0.0

    return sum(_real_loop_function(mno) for mno in itertools.product(*axis_ranges))


def _get_recip(
    conv,
    recip_axis,
    recip_volume,
    recip_latt,
    dielectric_matrix,
):
    # convert factional motif to reciprocal space and
    # calculate reciprocal space supercell parallelepiped
    recip_sup_latt = np.dot(np.diag(recip_axis), recip_latt)

    # Calculate reciprocal space component
    axis_ranges = [range(-a, a) for a in recip_axis]

    def _recip_loop_function(mno):
        # Calculate the defect's fractional position in extended supercell
        d_super = np.array(mno, dtype=float) / recip_axis
        d_super_cart = np.dot(d_super, recip_sup_latt)

        if any(mno):
            mod = np.dot(d_super_cart, dielectric_matrix)
            dot_prod = np.dot(mod, d_super_cart)
            return exp(-dot_prod / (4 * conv**2)) / dot_prod

        return 0.0

    reciprocal = sum(_recip_loop_function(mno) for mno in itertools.product(*axis_ranges))
    scale_factor = 4 * np.pi / recip_volume
    return reciprocal * scale_factor


def lany_zunger_corrected_defect_dict(defect_dict: dict):
    """
    Convert charge corrections from (e)FNV to Lany-Zunger in the input parsed
    defect dictionary.

    This function is used to convert the finite-size charge corrections for
    parsed defect entries in a dictionary to the same dictionary but with the
    Lany-Zunger charge correction (0.65 * Makov-Payne image charge correction,
    with the same potential alignment).

    Args:
        defect_dict (dict):
            Dictionary of parsed defect calculations. Must have
            ``'freysoldt_meta'`` in ``DefectEntry.calculation_metadata`` for
            each charged defect (from ``DefectParser.load_FNV_data()``).

    Returns:
        Parsed defect dictionary with Lany-Zunger charge corrections.
    """
    # Just need any DefectEntry from defect_dict to get the lattice and dielectric matrix
    random_defect_entry = next(iter(defect_dict.values()))
    lattice = _get_bulk_supercell(random_defect_entry).lattice.matrix
    dielectric = random_defect_entry.calculation_metadata["dielectric"]
    lz_image_charge_corrections = get_murphy_image_charge_correction(lattice, dielectric)
    lz_corrected_defect_dict = copy.deepcopy(defect_dict)
    for defect_name, defect_entry in lz_corrected_defect_dict.items():
        if defect_entry.charge_state != 0:
            if "freysoldt_meta" in defect_entry.calculation_metadata:
                potalign = defect_entry.calculation_metadata["freysoldt_meta"][
                    "freysoldt_potential_alignment_correction"
                ]
            else:
                potalign = defect_entry.calculation_metadata["kumagai_meta"][
                    "kumagai_potential_alignment_correction"
                ]
            mp_pc_corr = lz_image_charge_corrections[abs(defect_entry.charge_state)]  # Makov-Payne PC
            defect_entry.calculation_metadata.update(
                {
                    "Lany-Zunger_Corrections": {
                        "Potential_Alignment_Correction": potalign,
                        "Makov-Payne_Image_Charge_Correction": mp_pc_corr,
                        "Lany-Zunger_Scaled_Image_Charge_Correction": 0.65 * mp_pc_corr,
                        "Total_Lany-Zunger_Correction": potalign + 0.65 * mp_pc_corr,
                    }
                }
            )
            defect_entry.corrections = {
                "LZ_charge_correction": defect_entry.calculation_metadata["Lany-Zunger_Corrections"][
                    "Total_Lany-Zunger_Correction"
                ]
            }

        lz_corrected_defect_dict.update({defect_name: defect_entry})
    return lz_corrected_defect_dict
