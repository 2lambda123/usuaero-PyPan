import copy

import numpy as np

from abc import abstractmethod
from pypan.pp_math import vec_cross, vec_inner, vec_norm, norm, cross
from pypan.helpers import OneLineProgress
from pypan.filaments import SegmentedVortexFilament

class Wake:
    """A base class for wake models in PyPan. This class can be used as a dummy class for there being no wake.

    Parameters
    ----------
    kutta_edges : list of KuttaEdge
        List of Kutta edges which define this wake.
    """

    def __init__(self, **kwargs):

        # Store Kutta edges
        self._kutta_edges = kwargs["kutta_edges"]
        self._N_edges = len(self._kutta_edges)
        self.filaments = []


    def _arrange_kutta_vertices(self):
        # Determines a unique list of the vertices defining all Kutta edges for the wake and the panels associated with each vertex

        if self._N_edges>0:

            # Get array of all vertices
            vertices = np.zeros((2*self._N_edges,3))
            for i, edge in enumerate(self._kutta_edges):

                # Store vertices
                vertices[2*i] = edge.vertices[0]
                vertices[2*i+1] = edge.vertices[1]

            # Determine unique vertices
            unique_vertices, inverse_indices = np.unique(vertices, return_inverse=True, axis=0)

            # Initialize filaments
            inbound_panels = []
            outbound_panels = []
            for i, vertex in enumerate(unique_vertices):

                # Determine associated panels
                ip = []
                op = []
                for j, ind in enumerate(inverse_indices):
                    if ind==i:
                        if j%2==0: # Inbound node for these panels
                            ip = copy.copy(self._kutta_edges[j//2].panel_indices)
                        else: # Outbound node
                            op = copy.copy(self._kutta_edges[j//2].panel_indices)

                # Store panels
                inbound_panels.append(ip)
                outbound_panels.append(op)

        return unique_vertices, inbound_panels, outbound_panels


    def get_influence_matrix(self, **kwargs):
        """Create wake influence matrix; first index is the influenced panels (bordering the horseshoe vortex), second is the influencing panel, third is the velocity component.

        Parameters
        ----------
        points : ndarray
            Array of points at which to calculate the influence.

        u_inf : ndarray
            Freestream direction vector.
        
        omega : ndarray
            Body-fixed rotation rates.

        Returns
        -------
        ndarray
            Trailing vortex influences.
        """

        return 0.0


    def get_vtk_data(self, **kwargs):
        """Returns a list of vertices and line indices describing this wake.
        
        Parameters
        ----------
        length : float, optional
            Length each fixed vortex filament should be. Defaults to 5.0.
        """

        return [], [], 0

    
    def set_filament_direction(self, v_inf, omega):
        """Sets the initial direction of the vortex filaments."""
        pass


class NonIterativeWake(Wake):
    """Defines a non-iterative wake consisting of straight, semi-infinite vortex filaments.

    Parameters
    ----------
    kutta_edges : list of KuttaEdge
        List of Kutta edges which define this wake.

    type : str
        May be "custom", "freestream", "freestream_constrained", "freestream_and_rotation", or "freestream_and_rotation_constrained". Defaults to "freestream".

    dir : list or ndarray, optional
        Direction of the vortex filaments. Required for type "custom".

    normal_dir : list or ndarray, optional
        Normal direction of the plane in which the direction of the vortex filaments should be constrained. Required for type "freestream_constrainted" or "freestream_and_rotation_constrained".
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # Store type
        self._type = kwargs.get("type", "freestream")

        # Initialize filaments
        self._vertices, self.inbound_panels, self.outbound_panels = self._arrange_kutta_vertices()

        # Store number of filaments and segments
        self.N = len(self._vertices)
        self.N_segments = 1

        # Initialize filament directions
        self.filament_dirs = np.zeros((self.N, 3))

        # Get direction for custom wake
        if self._type=="custom":
            try:
                u = np.array(kwargs.get("dir"))
                u /= norm(u)
                self.filament_dirs[:] = u
            except:
                raise IOError("'dir' is required for non-iterative wake type 'custom'.")

        # Get normal direction for constrained wake
        if "constrained" in self._type:
            try:
                self._n = np.array(kwargs.get("normal_dir"))
                self._n /= norm(self._n)
            except:
                raise IOError("'normal_dir' is required for wake type {0}.".format(self._type))
            
            # Create projection matrix
            self._P = np.eye(3)-np.matmul(self._n[:,np.newaxis], self._n[np.newaxis,:])


    def set_filament_direction(self, v_inf, omega):
        """Updates the direction of the vortex filaments based on the velocity params.

        Parameters
        ----------
        v_inf : ndarray
            Freestream velocity vector.

        omega : ndarray
            Angular rate vector.
        """

        # Freestream direction
        if self._type=="freestream":
            u = v_inf/norm(v_inf)
            self.filament_dirs[:] = u

        # Freestream constrained
        elif self._type=="freestream_constrained":
            u = np.einsum('ij,j', self._P, v_inf)
            u /= norm(u)
            self.filament_dirs[:] = u

        # Freestream with rotation
        elif self._type=="freestream_and_rotation":
            self.filament_dirs = v_inf[np.newaxis,:]-vec_cross(omega, self._vertices)
            self.filament_dirs /= vec_norm(self.filament_dirs)[:,np.newaxis]

        # Freestream with rotation constrained
        elif self._type=="freestream_and_rotation_constrained":
            self.filament_dirs = v_inf[np.newaxis,:]-vec_cross(omega, self._vertices)
            self.filament_dirs = np.einsum('ij,kj->ki', self._P, self.filament_dirs)
            self.filament_dirs /= vec_norm(self.filament_dirs)[:,np.newaxis]


    def get_influence_matrix(self, **kwargs):
        """Create wake influence matrix; first index is the influenced panels, second is the influencing panel, third is the velocity component.

        Parameters
        ----------
        points : ndarray
            Array of points at which to calculate the influence.

        N_panels : int
            Number of panels in the mesh to which this wake belongs.

        u_inf : ndarray
            Freestream direction vector.
        
        omega : ndarray
            Body-fixed rotation rates.

        Returns
        -------
        ndarray
            Trailing vortex influences.
        """

        # Get kwargs
        points = kwargs.get("points")

        # Initialize storage
        N = len(points)
        vortex_influence_matrix = np.zeros((N, kwargs["N_panels"], 3))

        # Get influence of edges
        for edge in self._kutta_edges:

            # Get indices of panels defining the edge
            p_ind = edge.panel_indices

            # Get infulence
            V = edge.get_vortex_influence(points)

            # Store
            vortex_influence_matrix[:,p_ind[0]] = -V
            vortex_influence_matrix[:,p_ind[1]] = V

        # Determine displacement vector magnitudes
        r = points[:,np.newaxis,:]-self._vertices[np.newaxis,:,:]
        r_mag = vec_norm(r)

        # Calculate influences
        V = 0.25/np.pi*vec_cross(self.filament_dirs[np.newaxis,:,:], r)/(r_mag*(r_mag-vec_inner(self.filament_dirs[np.newaxis,:,:], r)))[:,:,np.newaxis]
        for i in range(self.N):

            # Add for outbound panels
            outbound_panels = self.outbound_panels[i]
            if len(outbound_panels)>0:
                vortex_influence_matrix[:,outbound_panels[0],:] -= V[:,i,:]
                vortex_influence_matrix[:,outbound_panels[1],:] += V[:,i,:]

            # Add for inbound panels
            inbound_panels = self.inbound_panels[i]
            if len(inbound_panels)>0:
                vortex_influence_matrix[:,inbound_panels[0],:] += V[:,i,:]
                vortex_influence_matrix[:,inbound_panels[1],:] -= V[:,i,:]
        
        return vortex_influence_matrix


    def get_vtk_data(self, **kwargs):
        """Returns a list of vertices and line indices describing this wake.
        
        Parameters
        ----------
        length : float, optional
            Length each vortex filament should be. Defaults to 5.0.
        """

        # Get kwargs
        l = kwargs.get("length", 5.0)

        # Initialize storage
        vertices = []
        line_vertex_indices = []

        # Loop through filaments
        i = 0
        for j in range(self.N):

            # Add vertices
            vertices.append(self._vertices[j])
            vertices.append(self._vertices[j]+l*self.filament_dirs[j])

            # Add indices
            line_vertex_indices.append([2, i, i+1])

            # Increment index
            i += 2

        return vertices, line_vertex_indices, self.N


class IterativeWake(Wake):
    """Defines an iterative wake consisting of segmented semi-infinite vortex filaments. Will initially be set in the direction of the local freestream vector resulting from the freestream velocity and rotation.

    Parameters
    ----------
    kutta_edges : list of KuttaEdge
        List of Kutta edges which define this wake.

    N_segments : int, optional
        Number of segments to use for each filament. Defaults to 20.

    segment_length : float, optional
        Length of each discrete filament segment. Defaults to 1.0.

    end_segment_infinite : bool, optional
        Whether the final segment of the filament should be treated as infinite. Defaults to False.

    corrector_iterations : int, optional
        How many times to correct the streamline prediction at each step. Defaults to 1.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # Get kwargs
        self.l = kwargs.get('segment_length', 1.0)
        self.N_segments = kwargs.get('N_segments', 20)
        self._corrector_iterations = kwargs.get('corrector_iterations', 1)

        # Initialize filaments
        self.filaments = []
        vertices, inbound_panels, outbound_panels = self._arrange_kutta_vertices()
        for vertex, ip, op in zip(vertices, inbound_panels, outbound_panels):
            self.filaments.append(SegmentedVortexFilament(vertex, ip, op, **kwargs))

        # Store number of filaments
        self.N = len(self.filaments)


    def set_filament_direction(self, v_inf, omega):
        """Updates the initial direction of the vortex filaments based on the velocity params.

        Parameters
        ----------
        v_inf : ndarray
            Freestream velocity vector.

        omega : ndarray
            Angular rate vector.
        """

        # Freestream with rotation
        for filament in self.filaments:
            u = v_inf-cross(omega, filament.p0)
            u /= norm(u)
            filament.initialize_points(u)


    def get_vtk_data(self, **kwargs):
        """Returns a list of vertices and line indices describing this wake.
        
        Parameters
        ----------
        length : float, optional
            Length of the final filament segment, if set as infinite.
        """

        # Get kwargs
        l = kwargs.get("length", 5.0)

        # Initialize storage
        vertices = []
        line_vertex_indices = []

        # Loop through filaments
        i = 0
        for filament in self.filaments:

            # Add vertices
            for j, vertex in enumerate(filament.points):
                vertices.append(vertex)

                # Add indices
                if j!=len(filament.points)-1:
                    line_vertex_indices.append([2, i+j, i+j+1])

            # Treat infinite end segment
            if filament.end_inf:
                u = vertices[-1]-vertices[-2]
                u /= norm(u)
                vertices[-1] = vertices[-2]+u*l

            # Increment index
            i += filament.points.shape[0]

        return vertices, line_vertex_indices, self.N*self.filaments[0].N


    def get_influence_matrix(self, **kwargs):
        """Create wake influence matrix; first index is the influenced panels
        (bordering the horseshoe vortex), second is the influencing panel, third is the 
        velocity component.

        Parameters
        ----------
        points : ndarray
            Array of points at which to calculate the influence.

        N_panels : int
            Number of panels in the mesh to which this wake belongs.

        u_inf : ndarray
            Freestream direction vector.
        
        omega : ndarray
            Body-fixed rotation rates.

        Returns
        -------
        ndarray
            Trailing vortex influences.
        """

        # Get kwargs
        points = kwargs.get("points")

        # Initialize storage
        N = len(points)
        vortex_influence_matrix = np.zeros((N, kwargs["N_panels"], 3))

        # Get influence of edges
        for edge in self._kutta_edges:

            # Get indices of panels defining the edge
            p_ind = edge.panel_indices

            # Get infulence
            V = edge.get_vortex_influence(points)

            # Store
            vortex_influence_matrix[:,p_ind[0]] = -V
            vortex_influence_matrix[:,p_ind[1]] = V

        # Get influence of filaments
        for filament in self.filaments:

            # Get influence
            V = filament.get_influence(points)

            # Add for outbound panels
            outbound_panels = filament.outbound_panels
            if len(outbound_panels)>0:
                vortex_influence_matrix[:,outbound_panels[0]] -= V
                vortex_influence_matrix[:,outbound_panels[1]] += V

            # Add for inbound panels
            inbound_panels = filament.inbound_panels
            if len(inbound_panels)>0:
                vortex_influence_matrix[:,inbound_panels[0]] += V
                vortex_influence_matrix[:,inbound_panels[1]] -= V
        
        return vortex_influence_matrix


    def update(self, velocity_from_body, mu, v_inf, omega, verbose):
        """Updates the shape of the wake based on solved flow results.

        Parameters
        ----------
        velocity_from_body : callable
            Function which will return the velocity induced by the body at a given set of points.

        mu : ndarray
            Vector of doublet strengths.

        v_inf : ndarray
            Freestream velocity vector.

        omega : ndarray
            Angular rate vector.

        verbose : bool
        """

        if verbose:
            print()
            prog = OneLineProgress(self.N_segments+1, msg="    Updating wake shape")

        # Initialize storage
        new_locs = np.zeros((self.N, self.N_segments, 3))

        # Get starting locations (offset slightly from origin to avoid singularities)
        curr_loc = np.zeros((self.N, 3))
        for i, filament in enumerate(self.filaments):
            curr_loc[i] = filament.p0+filament.dir*0.01
        
        if verbose: prog.display()

        # Loop through filament segments (the first vertex never changes)
        next_loc = np.zeros((self.N, 3))
        for i in range(1,self.N_segments+1):

            # Determine velocities at current point
            v0 = velocity_from_body(curr_loc)+v_inf[np.newaxis,:]-vec_cross(omega, curr_loc)
            v0 += self._get_velocity_from_other_filaments_and_edges(curr_loc, mu)

            # Guess of next location
            next_loc = curr_loc+self.l*v0/vec_norm(v0)[:,np.newaxis]

            # Iteratively correct
            for j in range(self._corrector_iterations):

                # Velocities at next location
                v1 = velocity_from_body(next_loc)+v_inf[np.newaxis,:]
                v1 += self._get_velocity_from_other_filaments_and_edges(next_loc, mu)

                # Correct location
                v_avg = 0.5*(v0+v1)
                next_loc = curr_loc+self.l*v_avg/vec_norm(v_avg)[:,np.newaxis]

            # Store
            new_locs[:,i-1,:] = np.copy(next_loc)

            # Move downstream
            curr_loc = np.copy(next_loc)

            if verbose: prog.display()

        # Store the new locations in the filaments.
        for i, filament in enumerate(self.filaments):
            filament.points[1:] = new_locs[i]


    def _get_velocity_from_other_filaments_and_edges(self, points, mu):
        # Determines the velocity at each point (assumed to be on a filament) induced by all other filaments and Kutta edges

        # Initialize storage
        v_ind = np.zeros((points.shape[0], 3))

        # Loop through points
        for i, filament in enumerate(self.filaments):

            # Get indices of points not belonging to this filament
            ind = [j for j in range(self.N) if j!=i]

            # Get influence vector
            v = filament.get_influence(points[ind])

            # Add for outbound panels
            outbound_panels = filament.outbound_panels
            if len(outbound_panels)>0:
                v_ind[ind] -= v*mu[outbound_panels[0]]
                v_ind[ind] += v*mu[outbound_panels[1]]

            # Add for inbound panels
            inbound_panels = filament.inbound_panels
            if len(inbound_panels)>0:
                v_ind[ind] += v*mu[inbound_panels[0]]
                v_ind[ind] -= v*mu[inbound_panels[1]]

        # Get influence of edges
        for edge in self._kutta_edges:

            # Get indices of panels defining the edge
            p_ind = edge.panel_indices

            # Get infulence
            v = edge.get_vortex_influence(points)

            # Store
            v_ind += -v*mu[p_ind[0]]
            v_ind += v*mu[p_ind[1]]

        return v_ind