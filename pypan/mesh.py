"""Defines classes for handling meshes."""

import time
import stl
import vtk
import warnings

import numpy as np
import pyvista as pv
import matplotlib.pyplot as plt

from mpl_toolkits.mplot3d import Axes3D
from vtk.util.numpy_support import vtk_to_numpy

from .pp_math import vec_cross, vec_inner, vec_norm, norm
from .helpers import OneLineProgress
from .geometry import Tri, Quad, KuttaEdge

class Mesh:
    """A class for defining collections of panels.

    Parameters
    ----------
    name : str
        Name of the mesh.

    mesh_file : str
        File path to the mesh file. Please note that PyPan assumes the panel
        normals all point outward. Failure to meet this condition can produce 
        erroneous results.

    mesh_file_type : str
        The type of mesh file being loaded. Can be "STL" or "VTK".

        Currently PyPan can import a VTK *unstructured mesh*. The panels should
        be given as POLYGONS. Kutta edges (if given) are listed simply as LINES.
        PyPan can accept no other format currently. Within a VTK file, the normal
        vector, area, and centroid may also be given under CELL_DATA. In all cases
        LOOKUP_TABLE should be default (PyPan is not currently able to parse non-
        default lookup tables).

    kutta_angle : float, optional
        The angle threshold for determining where the Kutta condition should
        be enforced. Defaults to None, in which case Kutta edges will not be 
        automatically determined.

        This is not needed for "VTK" type meshes where the Kutta edges are already
        specified. However, if given, this will force reevaluation of the Kutta
        edges (expensive!) whether or not were determined previously. If not given,
        the Kutta edges listed in the mesh file will be used.

    CG : list, optional
        Location of the center of gravity for the mesh. This is the location about 
        which moments are computed. Defaults to [0.0, 0.0, 0.0]. This is relative
        to the coordinate system of the mesh.
    """

    def __init__(self, **kwargs):

        # Load kwargs
        self.name = kwargs.get("name")
        mesh_file = kwargs.get("mesh_file")
        mesh_type = kwargs.get("mesh_file_type")
        self._verbose = kwargs.get("verbose", False)
        self.CG = np.array(kwargs.get("CG", [0.0, 0.0, 0.0]))

        # Load mesh
        if self._verbose:
            start_time = time.time()
            print("\nReading in mesh...", end='', flush=True)
        self._load_mesh(mesh_file, mesh_type)
        if self._verbose:
            end_time = time.time()
            print("Finished. Time: {0} s.".format(end_time-start_time), flush=True)

        # Create panel vertex mapping
        # VTK does this inherently; STL has a faster way than the brute-force method
        if mesh_type != "VTK" and mesh_type != "STL":
            self._determine_panel_vertex_mapping()

        # Determine panel adjacency mapping
        self._determine_panel_adjacency_mapping()

        # Find Kutta edges
        self._find_kutta_edges(**kwargs)

        # Calculate moment arms
        self.r_CG = self.cp-self.CG[np.newaxis,:]

        # Display mesh information
        if self._verbose:
            print("\nMesh Parameters:")
            print("    # panels: {0}".format(self.N))
            print("    # vertices: {0}".format(self._vertices.shape[0]))
            if hasattr(self, "N_edges"):
                print("    # Kutta edges: {0}".format(self.N_edges))

    
    def _load_mesh(self, mesh_file, mesh_file_type):
        # Loads the mesh from the input file

        # STL
        if mesh_file_type == "STL":
            self._load_stl(mesh_file)

        # VTK
        elif mesh_file_type == "VTK":
            self._load_vtk_mesh(mesh_file)

        # PAN AIR
        elif mesh_file_type == "PAN AIR":
            self._load_panair_mesh(mesh_file)

        # Unrecognized type
        else:
            raise IOError("{0} is not a supported mesh type for PyPan.".format(mesh_file_type))


    def _load_stl(self, stl_file):
        # Loads mesh from an stl file

        # Load stl file
        raw_mesh = stl.mesh.Mesh.from_file(stl_file)

        # Initialize storage
        N = raw_mesh.v0.shape[0]
        self.N = N
        self.panels = []
        bad_facets = []

        # Loop through panels and initialize objects
        for i in range(N):

            # Check for finite area
            if norm(raw_mesh.normals[i]) == 0.0:
                self.N -= 1
                warnings.warn("Panel {0} has zero area. Skipping...".format(i))
                bad_facets.append(i)
                continue

            # Initialize
            panel = Tri(v0=raw_mesh.v0[i],
                        v1=raw_mesh.v1[i],
                        v2=raw_mesh.v2[i],
                        n=raw_mesh.normals[i])

            self.panels.append(panel)

        self.panels = np.array(self.panels)

        # Store panel information
        self.cp = np.zeros((self.N, 3))
        self.n = np.zeros((self.N, 3))
        self.dA = np.zeros(self.N)
        for i in range(self.N):
            self.cp[i] = self.panels[i].v_c
            self.n[i] = self.panels[i].n
            self.dA[i] = self.panels[i].A

        # Get vertex list
        good_facets = [i for i in range(N) if i not in bad_facets]
        raw_vertices = np.concatenate((raw_mesh.v0[good_facets], raw_mesh.v1[good_facets], raw_mesh.v2[good_facets]))
        self._vertices, inverse_indices = np.unique(raw_vertices, return_inverse=True, axis=0)
        self._panel_vertex_indices = []
        for i in range(self.N):
            self._panel_vertex_indices.append([3, *inverse_indices[i::self.N]])

    
    def _load_vtk_mesh(self, vtk_file):
        # Loads mesh from PyPan file

        # Get data from file
        mesh_data = pv.read(vtk_file)

        # Get vertices
        self._vertices = np.copy(mesh_data.points)

        # Initialze storage
        self.N = mesh_data.n_faces
        self.cp = np.array(mesh_data.get_array('panel_centroids'))
        self.n = np.array(mesh_data.get_array('panel_normals'))
        self.dA = np.array(mesh_data.get_array('panel_area'))

        # Initialize panels
        self.panels = []
        self._panel_vertex_indices = []
        curr_ind = 0
        cell_info = mesh_data.faces
        self._poly_list_size = len(cell_info)
        for i in range(self.N):

            # Determine number of edges and vertex indices
            n = cell_info[curr_ind]
            vertex_ind = cell_info[curr_ind+1:curr_ind+1+n]
            self._panel_vertex_indices.append([n, *list(vertex_ind)])
            vertices = self._vertices[vertex_ind]

            # Initialize panel object
            if n==3:
                panel_obj = Tri(v0=vertices[0],
                                v1=vertices[1],
                                v2=vertices[2],
                                n=self.n[i],
                                v_c=self.cp[i],
                                A=self.dA[i])
            elif n==4:
                panel_obj = Quad(v0=vertices[0],
                                 v1=vertices[1],
                                 v2=vertices[2],
                                 v3=vertices[3],
                                 n=self.n[i],
                                 v_c=self.cp[i],
                                 A=self.dA[i])

            # Check for zero area
            if abs(panel_obj.A)<1e-10:
                raise IOError("Panel {0} in the mesh has zero area.".format(i))
            
            # Store
            self.panels.append(panel_obj)

            # Update index
            curr_ind += n+1


    def _load_panair_mesh(self, panair_file):
        # Reads in the structured mesh from a PAN AIR input file

        # Initialize storage
        vertices = []
        panels = []

        # Open file
        with open(panair_file, 'r') as input_handle:

            # Get to mesh points
            while "$POINTS" not in input_handle.readline():
                continue

            # Store points
            skip_next = False
            for line in input_handle.readlines():

                # Skip irrelevant lines
                if line[0]=="=":
                    skip_next = True
                    continue
                if "$POINTS" in line:
                    continue
                if skip_next:
                    skip_next = False
                    continue

                # End mesh parsing
                if "$FLOW-FIELD" in line:
                    break

                # Get points
                N_coords = len(line)/10
                N_vert = int(N_coords/3)
                for i in range(N_vert):
                    vertices.append([float(line[int(i*30):int(i*30+10)]), float(line[int(i*30+10):int(i*30+20)]), float(line[int(i*30+20):int(i*30+30)])])

        # Set up plot
        fig = plt.figure(figsize=plt.figaspect(1.0))
        ax = fig.gca(projection='3d')
        
        # Plot vertices
        for vertex in vertices:
            ax.plot(vertex[0], vertex[1], vertex[2], 'k.')

        ax.set_xlabel('x')
        ax.set_ylabel('y')
        ax.set_zlabel('z')
        plt.show()


    def _find_kutta_edges(self, **kwargs):
        # Determines where Kutta condition should exist; relies on an adjacency mapping already being created

        # Get Kutta angle
        theta_K = kwargs.get("kutta_angle", None)

        # Look for adjacent panels where the Kutta condition should be applied
        if theta_K is not None:
            theta_K = np.radians(theta_K)

            if self._verbose:
                print()
                prog = OneLineProgress(self.N, msg="Determining locations to apply Kutta condition")

            # Get panel angles
            with np.errstate(invalid='ignore'):
                theta = np.abs(np.arccos(np.einsum('ijk,ijk->ij', self.n[:,np.newaxis], self.n[np.newaxis,:])))

            # Determine which panels have an angle greater than the Kutta angle
            angle_greater = (theta>theta_K).astype(int)
            i_panels = np.argwhere(np.sum(angle_greater, axis=1).flatten()).flatten()

            # Initialize edge storage
            self.kutta_edges = []

            # Loop through possible combinations
            for i in i_panels:
                panel_i = self.panels[i]

                # Determine if we're adjacent
                j_panels = np.argwhere(angle_greater[i]).flatten()
                for j in panel_i.adjacent_panels:

                    # Don't repeat
                    if j <= i:
                        continue

                    # Check angle
                    if j in j_panels:
                        panel_j = self.panels[j]
                    
                        # Get edge vertices
                        v0 = None
                        for ii, vi in enumerate(panel_i.vertices):
                            for vj in panel_j.vertices:

                                # Check distance
                                d = norm(vi-vj)
                                if d<1e-10:

                                    # Store first
                                    if v0 is None:
                                        v0 = vi
                                        ii0 = ii

                                    # Initialize edge object; vertices are stored in the same order as the first panel
                                    else:
                                        if ii-ii0 == 1: # Order is important for definition of circulation
                                            self.kutta_edges.append(KuttaEdge(v0, vi, [i, j]))
                                        else:
                                            self.kutta_edges.append(KuttaEdge(vi, v0, [i, j]))
                                        break

                if self._verbose:
                    prog.display()


            self.N_edges = len(self.kutta_edges)

            # Store adjacent panels not across edge
            for i, panel in enumerate(self.panels):
                for j in panel.adjacent_panels:
                    if angle_greater[i,j]:
                        panel.adjacent_panels_across_kutta_edge.append(j)
                    else:
                        panel.adjacent_panels_not_across_kutta_edge.append(j)

        else:
            self.N_edges = 0


    def _check_for_vertex(self, vertex, v_list):
        # Checks for the vertex in the list; if there, the index is returned

        # Loop through list
        for i, v in enumerate(v_list):
            if np.allclose(v, vertex, atol=1e-8):
                return i
        
        return -1


    def _determine_panel_vertex_mapping(self):
        # Creates a list of all unique vertices and maps each panel to those vertices

        if self._verbose:
            print()
            prog = OneLineProgress(self.N, msg="Determining panel->vertex mapping")

        # Collect vertices and panel vertex indices
        self._vertices = []
        self._panel_vertex_indices = [] # First index is the number of vertices, the rest are the vertex indices
        self._poly_list_size = 0

        # Loop through panels
        i = 0 # Index of last added vertex
        for panel in self.panels:

            # Initialize panel info
            if isinstance(panel, Tri):
                panel_info = [3]
                self._poly_list_size += 4
            elif isinstance(panel, Quad):
                panel_info = [4]
                self._poly_list_size += 5

            # Check if vertices are in the list
            for vertex in panel.vertices:
                ind = self._check_for_vertex(vertex, self._vertices)

                # Not in list
                if ind == -1:
                    self._vertices.append(vertex)
                    panel_info.append(i)
                    i += 1

                # In list
                else:
                    panel_info.append(ind)

            # Store panel info
            self._panel_vertex_indices.append(panel_info)
            if self._verbose:
                prog.display()

        self._vertices = np.array(self._vertices) # Cannot do this for _panel_vertex_indices because the length of each list element is not necessarily the same


    def _determine_panel_adjacency_mapping(self):
        # Stores a list of the indices to each adjacent panel for each panel

        if self._verbose:
            print()
            prog = OneLineProgress(self.N, msg="Locating adjacent panels")

        # Loop through possible combinations
        for i, panel_i in enumerate(self.panels):

            for j in range(i+1, self.N):
                panel_j = self.panels[j]
                
                # Determine if we're adjacent
                for i_vert in self._panel_vertex_indices[i][1:]:

                    if i_vert in self._panel_vertex_indices[j][1:] and j not in panel_i.adjacent_panels:
                        panel_i.adjacent_panels.append(j)
                        panel_j.adjacent_panels.append(i)
                        break

            if self._verbose:
                prog.display()


    def plot(self, **kwargs):
        """Plots the mesh in 3D.

        Parameters
        ----------
        panels : bool, optional
            Whether to display panels. Defaults to True.

        centroids : bool, optional
            Whether to display centroids. Defaults to True.

        kutta_edges : bool, optional
            Whether to display the edges at which the Kutta condition will be enforced.
            Defaults to True.
        """

        # Set up plot
        fig = plt.figure(figsize=plt.figaspect(1.0))
        ax = fig.gca(projection='3d')
        
        # Plot vertices
        if kwargs.get("panels", True):
            for i, panel in enumerate(self.panels):
                n_vert = panel.vertices.shape[1]
                ind = [x%n_vert for x in range(n_vert+1)]
                ax.plot(panel.vertices[ind,0], panel.vertices[ind,1], panel.vertices[ind,2], 'k-', label='Panel' if i==0 else '')
        
        ## Plot adjacency
        #ind = 0
        #neighbors = self.panels[ind].adjacent_panels
        #ax.plot(self.panels[ind].v_c[0], self.panels[ind].v_c[1], self.panels[ind].v_c[2], 'r.')
        #for i in neighbors:
        #    ax.plot(self.panels[i].v_c[0], self.panels[i].v_c[1], self.panels[i].v_c[2], 'g.')
        
        # Plot centroids
        if kwargs.get("centroids", True):
            for i, panel in enumerate(self.panels):
                ax.plot(panel.v_c[0], panel.v_c[1], panel.v_c[2], 'r.', label='Centroid' if i==0 else '')

        # Plot Kutta edges
        if kwargs.get("kutta_edges", True) and hasattr(self, "kutta_edges"):
            for i, edge in enumerate(self.kutta_edges):
                ax.plot(edge.vertices[:,0], edge.vertices[:,1], edge.vertices[:,2], 'b', label='Kutta Edge' if i==0 else '')

        ax.set_xlabel('x')
        ax.set_ylabel('y')
        ax.set_zlabel('z')
        lims= ax.get_ylim()
        ax.set_xlim3d(lims[0], lims[1])
        ax.set_zlim3d(lims[0], lims[1])
        plt.legend()
        plt.show()


    def export_vtk(self, filename):
        """Exports the mesh to a VTK file. Please note this exports the mesh only.
        There is an export_vtk() method within the Solver class which will export
        the mesh along with the flow data.

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
            print("PyPan mesh file. Generated by PyPan, USU AeroLab (c) 2020.", file=export_handle)
            print("ASCII", file=export_handle)

            # Write dataset
            print("DATASET POLYDATA", file=export_handle)

            # Write vertices
            vertices, panel_indices = self.get_vtk_data()
            print("POINTS {0} float".format(len(vertices)), file=export_handle)
            for vertex in vertices:
                print("{0:<20.12}{1:<20.12}{2:<20.12}".format(*vertex), file=export_handle)

            # Determine polygon list size
            size = 0
            for pi in panel_indices:
                size += len(pi)

            # Write panel polygons
            print("POLYGONS {0} {1}".format(self.N, size), file=export_handle)
            for panel in panel_indices:
                print(" ".join([str(i) for i in panel]), file=export_handle)

            # Write Kutta edges

            # Write panel data
            print("CELL_DATA {0}".format(self.N), file=export_handle)

            # Area
            print("SCALARS panel_area float 1", file=export_handle)
            print("LOOKUP_TABLE default", file=export_handle)
            for dA in self.dA:
                print("{0:<20.12}".format(dA), file=export_handle)

            # Centroids
            print("VECTORS panel_centroids float", file=export_handle)
            for cp in self.cp:
                print("{0:<20.12} {1:<20.12} {2:<20.12}".format(cp[0], cp[1], cp[2]), file=export_handle)

            # Normals
            print("NORMALS panel_normals float", file=export_handle)
            for n in self.n:
                print("{0:<20.12} {1:<20.12} {2:<20.12}".format(n[0], n[1], n[2]), file=export_handle)


    def get_vtk_data(self):
        """Returns a list of vertices and a list of indices referencing each panel to
        its vertices in the first list.
        """
        return self._vertices, self._panel_vertex_indices


    def get_gradient(self, phi):
        """Returns a least-squares estimate of the gradient of phi at each panel
        centroid. Phi should be given as the value of a scalar function at each
        panel centroid, in the correct order.

        Parameters
        ----------
        phi : ndarray
            Value of the scalar field at each panel centroid.

        Returns
        -------
        grad_phi : ndarray
            The gradient of phi at each panel centroid wrt the Cartesian axes.
        """

        # Initialize
        grad_phi = np.zeros((self.N, 3))

        # Loop through panels
        for i, panel in enumerate(self.panels):

            # Get centroids of neighboring panels
            neighbors = panel.adjacent_panels_not_across_kutta_edge
            neighbor_centroids = self.cp[neighbors]
            neighbor_phis = phi[neighbors]

            # Get A and b
            A = neighbor_centroids-panel.v_c[np.newaxis,:]
            b = neighbor_phis-phi[i]

            # Solve
            grad_phi[i] = np.linalg.solve(np.einsum('ij,ik', A, A), np.einsum('ij,i', A, b))

        return grad_phi