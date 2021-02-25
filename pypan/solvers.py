import time
import os

import numpy as np
import matplotlib.pyplot as plt

from abc import abstractmethod
from pypan.pp_math import vec_inner, vec_norm, norm

class Solver:
    """Base class for solvers."""

    def __init__(self, **kwargs):

        # Store mesh
        self._mesh = kwargs["mesh"]
        self._verbose = kwargs.get("verbose", False)

        # Gather control point locations and normals
        self._N_panels = self._mesh.N
        self._N_edges = self._mesh.N_edges

        # Determine projection matrix onto plane of each panel
        self._P_surf = np.repeat(np.identity(3)[np.newaxis,:,:], self._N_panels, axis=0)-np.matmul(self._mesh.n[:,:,np.newaxis], self._mesh.n[:,np.newaxis,:])


    def export_vtk(self, filename):
        """Exports the mesh(es) and solver results to a VTK file.

        Parameters
        ----------
        filename : str
            Name of the file to write the results to. Must have '.vtk' extension.
        """

        # Check extension
        if '.vtk' not in filename:
            raise IOError("Filename for VTK export must contain .vtk extension.")

        # Open file
        with open(filename, 'w') as export_handle:
            
            # Write header
            print("# vtk DataFile Version 3.0", file=export_handle)
            print("PyPan results file. Generated by PyPan, USU AeroLab (c) 2020.", file=export_handle)
            print("ASCII", file=export_handle)

            # Write dataset
            print("DATASET POLYDATA", file=export_handle)

            # Write vertices
            vertices, panel_indices = self._mesh.get_vtk_data()
            print("POINTS {0} float".format(len(vertices)), file=export_handle)
            for vertex in vertices:
                print("{0:<20.12}{1:<20.12}{2:<20.12}".format(*vertex), file=export_handle)

            # Determine polygon list size
            size = 0
            for pi in panel_indices:
                size += len(pi)

            # Write panel polygons
            print("POLYGONS {0} {1}".format(self._N_panels, size), file=export_handle)
            for panel in panel_indices:
                print(" ".join([str(i) for i in panel]), file=export_handle)

            # Write flow results
            print("CELL_DATA {0}".format(self._N_panels), file=export_handle)

            # Normals
            print("NORMALS panel_normals float", file=export_handle)
            for n in self._mesh.n:
                print("{0:<20.12} {1:<20.12} {2:<20.12}".format(n[0], n[1], n[2]), file=export_handle)

            # Pressure coefficient
            print("SCALARS pressure_coefficient float 1", file=export_handle)
            print("LOOKUP_TABLE default", file=export_handle)
            for C_P in self._C_P:
                print("{0:<20.12}".format(C_P), file=export_handle)

            # Potential
            if hasattr(self, "_mu"):
                print("SCALARS doublet_strength float 1", file=export_handle)
                print("LOOKUP_TABLE default", file=export_handle)
                for mu in self._mu:
                    print("{0:<20.12}".format(mu), file=export_handle)

            # Velocity
            if hasattr(self, "_v"):
                print("VECTORS velocity float", file=export_handle)
                for v in self._v:
                    print("{0:<20.12} {1:<20.12} {2:<20.12}".format(v[0], v[1], v[2]), file=export_handle)

                # Normal velocity
                print("SCALARS normal_velocity float", file=export_handle)
                print("LOOKUP_TABLE default", file=export_handle)
                for v_n in vec_inner(self._v, self._mesh.n):
                    print("{0:<20.12}".format(v_n), file=export_handle)

            # Gradient of potential
            if hasattr(self, "_grad_mu"):
                print("VECTORS doublet_gradient float", file=export_handle)
                for grad_mu in self._grad_mu:
                    print("{0:<20.12} {1:<20.12} {2:<20.12}".format(grad_mu[0], grad_mu[1], grad_mu[2]), file=export_handle)

        if self._verbose:
            print()
            print("Case results successfully written to '{0}'.".format(filename))


    def alpha_sweep(self, **kwargs):
        """Sweeps the solver through a range of angle of attack. Note this will always solve the lifting case.

        Parameters
        ----------
        V_inf : float
            Freestream velocity magnitude.

        alpha_lims : list
            Limits in angle of attack for the sweep, given in degrees.

        N_alpha : int, optional
            Number of angles of attack to solve within the range specified
            by alpha_lims. Defaults to 10.

        rho : float
            Freestream atmospheric density.

        results_dir : str, optional
            File path to a directory where the results at each step in alpha can be
            stored. If not given, the results will not be stored. Should end with '/'.

        Returns
        -------
        alphas : ndarray
            Angles of attack for the sweep, given in degrees.

        F : ndarray
            An array of the force vector at each angle of attack, given in mesh coordinates.

        F_w : ndarray
            An array of the force vector at each angle of attack, given in wind coordinates.
        """

        # Determine alpha range
        a_lims = kwargs["alpha_lims"]
        N_a = kwargs.get("N_alpha", 10)
        alphas = np.linspace(np.radians(a_lims[0]), np.radians(a_lims[1]), N_a)
        C_a = np.cos(alphas)
        S_a = np.sin(alphas)

        # Get other kwargs
        V_inf = kwargs.pop("V_inf")
        results_dir = kwargs.get("results_dir", None)
        if not os.path.exists(results_dir):
            os.mkdir(results_dir)

        # Initialize storage
        F = np.zeros((N_a, 3))

        # Loop through alphas
        for i, a in enumerate(alphas):

            # Determine freestream vector
            V = [-V_inf*C_a[i], 0.0, -V_inf*S_a[i]]

            # Set condition
            self.set_condition(V_inf=V, **kwargs)

            # Solve
            F[i] = self.solve(lifting=True)

            # Export
            if results_dir is not None:
                self.export_vtk(results_dir+"a_{0}.vtk".format(np.degrees(a)))

        # Convert to wind coordinates
        F_w = np.zeros((N_a, 3))
        F_w[:,0] = -C_a*F[:,0]-S_a*F[:,2]
        F_w[:,1] = F[:,1]
        F_w[:,2] = S_a*F[:,0]-C_a*F[:,2]

        return np.degrees(alphas), F, F_w


    def export_case_data(self, filename):
        """Writes the case data to the given file.

        Parameters
        ----------
        filename : str
            File location at which to store the case data.
        """

        # Setup data table
        item_types = [("cpx", "float"),
                      ("cpy", "float"),
                      ("cpz", "float"),
                      ("nx", "float"),
                      ("ny", "float"),
                      ("nz", "float"),
                      ("area", "float"),
                      ("u", "float"),
                      ("v", "float"),
                      ("w", "float"),
                      ("V", "float"),
                      ("C_P", "float"),
                      ("dFx", "float"),
                      ("dFy", "float"),
                      ("dFz", "float"),
                      ("circ", "float")]

        table_data = np.zeros(self._N_panels, dtype=item_types)

        # Geometry
        table_data[:]["cpx"] = self._mesh.cp[:,0]
        table_data[:]["cpy"] = self._mesh.cp[:,1]
        table_data[:]["cpz"] = self._mesh.cp[:,2]
        table_data[:]["nx"] = self._mesh.n[:,0]
        table_data[:]["ny"] = self._mesh.n[:,1]
        table_data[:]["nz"] = self._mesh.n[:,2]
        table_data[:]["area"] = self._mesh.dA

        # Velocities
        table_data[:]["u"] = self._v[:,0]
        table_data[:]["v"] = self._v[:,1]
        table_data[:]["w"] = self._v[:,2]
        table_data[:]["V"] = self._V
        table_data[:]["C_P"] = self._C_P

        # Circulation and forces
        table_data[:]["dFx"] = self._dF[:,0]
        table_data[:]["dFy"] = self._dF[:,1]
        table_data[:]["dFz"] = self._dF[:,2]
        table_data[:]["circ"] = self._mu[:self._N_panels]

        # Define header and output
        header = "{:<21}{:<21}{:<21}{:<21}{:<21}{:<21}{:<21}{:<21}{:<21}{:<21}{:<21}{:<21}{:<21}{:<21}{:<21}{:<21}".format(
                 "Control (x)", "Control (y)", "Control (z)", "nx", "ny", "nz", "Area", "u", "v", "w", "V", "C_P", "dFx", "dFy",
                 "dFz", "circ")
        format_string = "%20.12e %20.12e %20.12e %20.12e %20.12e %20.12e %20.12e %20.12e %20.12e %20.12e %20.12e %20.12e %20.12e %20.12e %20.12e %20.12e"

        # Save
        np.savetxt(filename, table_data, fmt=format_string, header=header)

    
    @abstractmethod
    def set_condition(self, **kwargs):
        """Sets the aerodynamic condition. Specific behavior is defined in the derived classes."""
        pass


    @abstractmethod
    def solve(self, **kwargs):
        """Solves the aerodynamics. Specific behavior is defined in the derived classes."""
        pass