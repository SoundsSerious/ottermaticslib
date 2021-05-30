import attr
import numpy
import functools
import shapely
import pandas
from shapely.geometry import Polygon, Point

from ottermatics.tabulation import TABLE_TYPES, NUMERIC_VALIDATOR, table_property
from ottermatics.configuration import otterize, Configuration
from ottermatics.components import Component
from ottermatics.analysis import Analysis
from ottermatics.solid_materials import *


import sectionproperties
import sectionproperties.pre.sections as sections
from sectionproperties.analysis.cross_section import CrossSection

import PyNite as pynite
from PyNite import Visualization

SECTIONS = {k:v for k,v in filter( lambda kv: issubclass(kv[1],sectionproperties.pre.sections.Geometry) if type(kv[1])  is type else False , sections.__dict__.items()) }



nonetype = type(None)

#TODO: Make analysis, where each load case is a row
@otterize
class Structure(Analysis):
    '''A integration between sectionproperties and PyNite, with a focus on ease of use

    Right now we just need an integration between Sections+Materials and Members, to find, CG, and inertial components

    Possible Future Additions:
    1) Structure Motion (free body ie vehicle , multi-strucutre ie robots)
    '''
    frame = None
    _beams = None

    def __on_init__(self):
        self.frame = pynite.FEModel3D()
        self._beams = {} #this is for us!

    @property
    def nodes(self):
        return self.frame.Nodes
    
    @property
    def members(self):
        return self.frame.Members

    @property
    def beams(self):
        return self._beams

    def add_node(self,name,x,y,z):
        self.frame.AddNode(name,x,y,z)

    def add_constraint(self,node,con_DX=False, con_DY=False, con_DZ=False, con_RX=False, con_RY=False, con_RZ=False):
        self.frame.DefineSupport(node,con_DX, con_DY, con_DZ, con_RX, con_RY, con_RZ)

    def add_member(self,name,node1,node2,section,material):
        assert node1 in self.nodes 
        assert node2 in self.nodes
        
        B = beam = Beam(self,name,material,section)
        self._beams[name] = beam

        self.frame.AddMember(name, node1, node2, B.E, B.G, B.Iy, B.Ix, B.J, B.A)

        return beam

    def add_member_with(self,name,node1,node2,E, G, Iy, Ix, J, A):
        '''a way to add specific beam properties to calculate stress,
        This way will currently not caluclate resulatant beam load.
        #TOOD: Add in a mock section_properties for our own stress calcs
        '''
        assert node1 in self.nodes 
        assert node2 in self.nodes
        
        material = SolidMaterial(density=0,elastic_modulus=E)
        B = beam = Beam(self,name,material,in_Iy=Iy,in_Ix=Ix,in_J=J,in_A=A,section=None)
        self._beams[name] = beam

        self.frame.AddMember(name, node1, node2, E, G, Iy, Ix, J, A)

        return beam        

    def analyze(self,**kwargs):
        return self.frame.Analyze(**kwargs)

    @property
    def cog(self):
        XM = sum([bm.mass * bm.centroid3d for bm in self.beams.values()])
        return XM / self.mass

    @property
    def mass(self):
        return sum([bm.mass for bm in self.beams.values()])

    def visulize(self,**kwargs):
        Visualization.RenderModel(self.frame,**kwargs)

    @property
    def node_dataframes(self):
        out = {}
        for case in self.frame.LoadCombos:
            rows = []
            for node in self.nodes.values():
                row = {'name':node.Name, 'dx':node.DX[case],'dy':node.DY[case],'dz':node.DZ[case], 'rx': node.RX[case],'ry':node.RY[case],'rz':node.RZ[case],'rxfx':node.RxnFX[case],'rxfy':node.RxnFY[case],'rxfz':node.RxnFZ[case],'rxmx':node.RxnMX[case],'rxmy':node.RxnMY[case],'rxmz':node.RxnMZ[case]}
                rows.append(row)
            
            out[case] = pandas.DataFrame(rows)
        return out
        







@otterize
class Beam(Component):
    '''Beam is a wrapper for emergent useful properties of the structure'''
    structure = attr.ib() #parent structure, will be in its _beams
    name = attr.ib()
    material = attr.ib(validator=attr.validators.instance_of(SolidMaterial))
    section = attr.ib(validator=attr.validators.instance_of((sectionproperties.pre.sections.Geometry,type(None))))

    mesh_size = attr.ib(default=3)

    in_Iy=attr.ib(default=None,validator=attr.validators.instance_of((int,float,nonetype)))
    in_Ix=attr.ib(default=None,validator=attr.validators.instance_of((int,float,nonetype))) 
    in_J=attr.ib(default=None,validator=attr.validators.instance_of((int,float,nonetype))) 
    in_A=attr.ib(default=None,validator=attr.validators.instance_of((int,float,nonetype)))  
    
    _L = None
    _section_properties = None
    _ITensor = None    

    min_stress_xy = None #set to true or false

    def __on_init__(self):
        self._skip_attr = ['mesh_size','in_Iy','in_Ix','in_J','in_A']

        self.debug('determining section properties...')
        if self.section is not None:
            self._mesh = self.section.create_mesh([self.mesh_size])
            self._section_properties = CrossSection(self.section, self._mesh) #no material here
            self._section_properties.calculate_geometric_properties()
            self._section_properties.calculate_warping_properties()
        else:
            assert all([val is not None for val in (self.in_Iy,self.in_Ix,self.in_J,self.in_A)])

    @table_property
    def L(self):
        return self.length

    @table_property
    def length(self):
        if self._L is None:
            self._L = self.member.L()
        return self._L

    @property
    def member(self):
        return self.structure.members[self.name]

    @functools.cached_property
    def n1(self):
        return self.member.iNode

    @functools.cached_property
    def n2(self):
        return self.member.jNode

    @functools.cached_property
    def P1(self):
        return numpy.array([self.n1.X,self.n1.Y,self.n1.Z])

    @functools.cached_property
    def P2(self):
        return numpy.array([self.n2.X,self.n2.Y,self.n2.Z])

    @table_property
    def E(self):
        return self.material.E

    @table_property
    def G(self):
        return self.material.G        

    @functools.cached_property
    def ITensor(self):
        if self._ITensor is None:
            (ixx_c, iyy_c, ixy_c) = self._section_properties.get_ic()
            _ITensor = [[ixx_c, ixy_c],
                        [ixy_c, iyy_c]]
            self._ITensor = numpy.array(_ITensor)
        return self._ITensor
            

    @table_property
    def Iy(self):
        if self.in_Iy is None:
            self.in_Iy = self.ITensor[1,1]
        return self.in_Iy

    @table_property
    def Ix(self):
        if self.in_Ix is None:
            self.in_Ix = self.ITensor[0,0]
        return self.in_Ix

    @table_property
    def Ixy(self):
        return self.ITensor[0,1]

    @table_property
    def J(self):
        if self.in_J is None:
            self.in_J = self._section_properties.get_j()
        return self.in_J

    @table_property
    def A(self):
        if self.in_A is None:
            self.in_A = self._section_properties.get_area()
        return self.in_A      

    @functools.cached_property
    def Vol(self):
        return self.A * self.L

    @functools.cached_property
    def section_mass(self):
        return self.material.density * self.A

    @functools.cached_property
    def mass(self):
        return self.material.density * self.Vol

    @functools.cached_property
    def cost(self):
        return self.mass * self.material.cost_per_kg

    @functools.cached_property
    def centroid2d(self):
        return self._section_properties.get_c()

    @functools.cached_property
    def centroid3d(self):
        return (self.P2 -self.P1) / 2.0 + self.P1

    def section_results(self):
        return self._section_properties.display_results()

    def show_mesh(self):
        return self._section_properties.plot_mesh()

    @table_property
    def max_von_mises(self):
        '''The worst of the worst cases, after adjusting the beem orientation for best loading'''
        return numpy.nanmax([self.max_von_mises_by_case])

    @property
    def max_von_mises_by_case(self):
        '''Gathers max vonmises stress info per case'''
        cmprv = {}
        for rxy in [True,False]:
            new = []
            out = self.von_mises_stress_l(rxy)
            for cmbo,vm_stress_vec in out.items():
                new.append(numpy.nanmax( vm_stress_vec ))

            cmprv[rxy] = numpy.array(new)

        vt = numpy.nanmax( cmprv[True] )
        vf = numpy.nanmax( cmprv[False] )

        #We choose the case with the 
        if vf < vt:
            self.min_stress_xy = False
            return vf 

        self.min_stress_xy = True
        return vt


    #TODO: Breakout other stress vectors
    def von_mises_stress_l(self,reverse_xy=None):
        '''Max von-mises stress'''

        if reverse_xy is None:
            if self.min_stress_xy is not None:
                reverse_xy = self.min_stress_xy
            else:
                reverse_xy = False
        

        out = {}
        for combo in self.structure.frame.LoadCombos:

            rows = []
            for i in numpy.linspace(0,1,11):
                inp  = dict(N=self.member.Axial(i,combo),
                            Vx=self.member.Shear('Fz' if not reverse_xy else 'Fy' ,i,combo),
                            Vy=self.member.Shear('Fy' if not reverse_xy else 'Fz' ,i,combo),
                            Mxx=self.member.Moment('Mz' if not reverse_xy else 'My',i,combo), 
                            Myy=self.member.Moment( 'My' if not reverse_xy else 'Mz',i,combo), 
                            M11=0, 
                            M22=0, 
                            Mzz=self.member.Torsion(i,combo) )
                
                sol = self._section_properties.calculate_stress(**inp)
                mat_stresses = sol.get_stress()

                max_vm =  numpy.nanmax([numpy.nanmax(stresses['sig_vm']) for stresses in mat_stresses])
                rows.append(max_vm )

            out[combo] = numpy.array(rows)
        
        return out

    def stress_info(self,reverse_xy=None):
        '''Max profile stress info along beam for each type'''

        if reverse_xy is None:
            if self.min_stress_xy is not None:
                reverse_xy = self.min_stress_xy
            else:
                reverse_xy = False

        out = {}
        for combo in self.structure.frame.LoadCombos:

            rows = []
            for i in numpy.linspace(0,1,11):
                sol = self.get_stress_at(i,combo,reverse_xy)
                mat_stresses = sol.get_stress()
                oout = {'x':x}
                for stresses in mat_stresses:
                    vals = {sn+'_'+stresses['Material']:numpy.nanmax(stress) for sn,stress in stresses.items() if 
                    isinstance(stress,numpy.ndarray)}
                    oout.update(vals)
            out[combo] = pandas.DataFrame(rows)
        
        return out

    def get_stress_at(self,x,combo,reverse_xy=None):
        '''gets stress at x, for load case combo'''
        
        if reverse_xy is None:
            if self.min_stress_xy is not None:
                reverse_xy = self.min_stress_xy
            else:
                reverse_xy = False        

        inp  = dict(N=self.member.Axial(x,combo),
                    Vx=self.member.Shear('Fz' if not reverse_xy else 'Fy' ,x,combo),
                    Vy=self.member.Shear('Fy' if not reverse_xy else 'Fz' ,x,combo),
                    Mxx=self.member.Moment('Mz' if not reverse_xy else 'My',x,combo), 
                    Myy=self.member.Moment( 'My' if not reverse_xy else 'Mz',x,combo), 
                    M11=0, 
                    M22=0, 
                    Mzz=self.member.Torsion(x,combo) )
        
        return self._section_properties.calculate_stress(**inp)



    @property
    def results(self):
        '''Min and max stress and deflection dataframes per case'''
        rows = []
        for combo in self.structure.frame.LoadCombos:
            mem = self.member
            row = dict( case = combo,
                        Iy = self.Iy,
                        Ix = self.Ix,
                        A = self.A,
                        E = self.E,
                        J = self.J,
                        G = self.G,
                        max_axial = mem.MaxAxial(combo),
                        min_axial = mem.MinAxial(combo),
                        max_my = mem.MaxMoment('My',combo),
                        min_my = mem.MinMoment('My',combo),
                        max_mz = mem.MaxMoment('Mz',combo),
                        min_mz = mem.MinMoment('Mz',combo),
                        max_shear_y = mem.MaxShear('Fy',combo),
                        min_shear_y = mem.MinShear('Fy',combo),
                        max_shear_z = mem.MaxShear('Fz',combo),
                        min_shear_z = mem.MinShear('Fz',combo),
                        max_torsion = mem.MaxTorsion(combo),
                        min_torsion = mem.MinTorsion(combo),
                        max_deflection_y = mem.MaxDeflection('dy',combo),
                        min_deflection_y = mem.MinDeflection('dy',combo),
                        max_deflection_x = mem.MaxDeflection('dx',combo),
                        min_deflection_x = mem.MinDeflection('dx',combo)                     
                        )

            rows.append(row)

        return pandas.DataFrame(rows)



































if __name__ == '__main__':

    import unittest
    from matplotlib.pylab import *

    class test_cantilever(unittest.TestCase):
        #tests the first example here
        #https://www.engineeringtoolbox.com/cantilever-beams-d_1848.html

        def setUp(self):

            self.st = Structure(name = 'cantilever_beam')
            self.st.add_node('wall',0,0,0)
            self.st.add_node('free',0,5,0)

            self.ibeam = sections.ISection(0.3072,0.1243,0.0121,0.008,0.0089,4)
            self.bm = self.st.add_member('mem','wall','free',material = ANSI_4130(), section = self.ibeam )

            self.st.add_constraint('wall',con_DX=True, con_DY=True, con_DZ=True, con_RY=True,con_RX=True,con_RZ=True)
            self.st.frame.AddNodeLoad('free','FX',3000)

            self.st.analyze(check_statics=True)

        def test_beam(self):

            self.subtest_assert_near(self.bm.A, 53.4/(100**2))
            self.subtest_assert_near(self.bm.Ix, 8196/(100**4))
            self.subtest_assert_near(self.bm.Iy, 388.8/(100**4))
            self.subtest_assert_near(self.bm.section_mass , 41.9)

            self.subtest_assert_near(self.bm.max_von_mises , 27.4E6 )
            self.subtest_assert_near(float(self.bm.results['min_deflection_y']) , -0.0076 )
            self.subtest_assert_near(float(self.bm.results['max_shear_y']) , 3000 )
            self.subtest_assert_near(float(self.bm.results['max_shear_y']) , 3000 )

            df = self.st.node_dataframes['Combo 1']

            dfw = df[df['name']=='wall']
            dff = df[df['name']=='free']

            self.subtest_assert_near(float(dfw['rxfx']), -3000)
            self.subtest_assert_near(float(dfw['rxmz']), 15000)
            self.subtest_assert_near(float(dfw['dx']), 0)
            
            self.subtest_assert_near(float(dff['dx']), 0.0076)
            self.subtest_assert_near(float(dff['rxfx']), 0)
            self.subtest_assert_near(float(dff['rxmz']), 0)

            stress_obj = self.bm.get_stress_at(0,'Combo 1')
            stress_obj.plot_stress_vm()


        def subtest_assert_near( self, value, truth, pct=0.025):
            with self.subTest():
                self.assertAlmostEqual( value, truth, delta = abs(truth*pct))


    class test_truss(unittest.TestCase):
        #Match this example, no beam stresses
        #https://engineeringlibrary.org/reference/trusses-air-force-stress-manual

        def setUp(self):

            self.st = Structure(name = 'truss')
            self.st.add_node('A',0,0,0)
            self.st.add_node('B',15,30* sqrt(3) / 2,0)
            self.st.add_node('C',45,30* sqrt(3) / 2,0)
            self.st.add_node('D',75,30* sqrt(3) / 2,0)
            self.st.add_node('E',90,0,0)
            self.st.add_node('F',60,0,0)
            self.st.add_node('G',30,0,0)

            pairs = set()
            Lmin = 30 * sqrt(3) / 2
            Lmax = 30.1

            for n1 in self.st.nodes.values():
                for n2 in self.st.nodes.values():
                    
                    L = numpy.sqrt((n1.X - n2.X)**2.0 + (n1.Y - n2.Y)**2.0 + (n1.Z - n2.Z)**2.0)

                    if L >= Lmin and L <= Lmax and (n1.Name,n2.Name) not in pairs and (n2.Name,n1.Name) not in pairs:
                        #print(f'adding {(n1.Name,n2.Name)}')
                        pairs.add((n1.Name,n2.Name))
                    
                    elif (n1.Name,n2.Name) in pairs or (n2.Name,n1.Name) in pairs:
                        pass
                        #print(f'skipping {(n1.Name,n2.Name)}, already in pairs')

            self.beam = sections.RectangularSection(0.5,0.5)
            

            constrained = ('A','E')
            for n1,n2 in pairs:
                bkey = f'{n1}_{n2}'
                self.bm = self.st.add_member(bkey,n1,n2,material = ANSI_4130(), section = self.beam )
                

                # if n1 not in constrained:
                #     print(f'releasing {bkey}')
                #     self.st.frame.DefineReleases(bkey, Rzi=True)

                # if n2 not in constrained:
                #     print(f'releasing {bkey} J')
                #     self.st.frame.DefineReleases(bkey, Rzj=True)



            self.st.add_constraint('A',con_DX=True, con_DY=True, con_DZ=True, con_RY=True,con_RX=True,con_RZ=True)
            self.st.add_constraint('E',con_DX=True, con_DY=True, con_DZ=True, con_RY=True,con_RX=True,con_RZ=True)
            for node in self.st.nodes:
                self.st.frame.DefineSupport(node,SupportDZ=True,SupportRZ=True)
                
            self.st.frame.AddNodeLoad('F','FY',-1000)
            self.st.frame.AddNodeLoad('G','FY',-2000)

            self.st.analyze(check_statics=True)

            print(self.st.node_dataframes)

        def test_reactions(self):

            df = self.st.node_dataframes['Combo 1']

            dfa = df[df['name']=='A']
            dfe = df[df['name']=='E']

            self.subtest_assert_near(float(dfa['rxfy']), 1667)
            self.subtest_assert_near(float(dfe['rxfy']), 1333)
            
            self.subtest_member('A','B','max_axial',1925)
            self.subtest_member('A','G','max_axial',-926)
            self.subtest_member('B','C','max_axial',1925)
            self.subtest_member('B','G','max_axial',-1925)
            self.subtest_member('C','D','max_axial',1541)
            self.subtest_member('F','G','max_axial',-1734)
            self.subtest_member('C','F','max_axial',382)
            self.subtest_member('C','G','max_axial',-382)
            self.subtest_member('C','G','max_axial',1541)
            self.subtest_member('D','F','max_axial',-1541)
            self.subtest_member('E','F','max_axial',-770)
            
            #Visualization.RenderModel( self.st.frame )

        def subtest_member(self,nodea,nodeb,result_key,truth,pct=0.025):
            key_1 = f'{nodea}_{nodeb}'
            key_2 = f'{nodeb}_{nodea}'
            
            if key_1 in self.st.beams:
                key = key_1
            elif key_2 in self.st.beams:
                key = key_2
            else:
                raise

            value = self.get_member_result(nodea,nodeb,result_key)
            dopasst = abs(value-truth) <= abs(truth)*pct

            if not dopasst:
                print(f'fails {key} {result_key}| {value:3.5f} == {truth:3.5f}?')
            self.subtest_assert_near(value,truth,pct=pct)


        def subtest_assert_near( self, value, truth, pct=0.025):
            with self.subTest():
                self.assertAlmostEqual( value, truth, delta = abs(truth*pct))
            
        
        def get_member_result(self,nodea,nodeb,result_key):
            key_1 = f'{nodea}_{nodeb}'
            key_2 = f'{nodeb}_{nodea}'

            if key_1 in self.st.beams:
                mem = self.st.beams[key_1]
                if result_key in mem.results:
                    return float(mem.results[result_key])
            
            elif key_2 in self.st.beams:
                mem = self.st.beams[key_2]
                if result_key in mem.results:
                    return float(mem.results[result_key])
                
            return numpy.nan #shouod fail, nan is not comparable

            

    unittest.main()


    # tst = Structure(name = 'tetst')
    # tst.add_node('k',0,0,-50)
    # tst.add_node('o',0,0,0)
    # tst.add_node('t',0,0,50)

    # tst.add_member('btm','k','o',material = SS_316(), section = sections.Rhs(1,0.5,0.1,0.1,5))
    # bm = tst.add_member('top','o','t',material = Aluminum(), section = sections.Chs(1.0,0.06,60) )
    
    # tst.add_constraint('k',*(True,)*6)
    # tst.frame.AddNodeLoad('t','FX',10000)
    # tst.frame.AddNodeLoad('t','FY',1)
    # tst.frame.AddNodeLoad('t','FZ',1)

    # tst.analyze(check_statics=True)

    # print(bm.results())
    # #tst.visulize()

    #Result Checked Against
    #https://www.engineeringtoolbox.com/cantilever-beams-d_1848.html











#This was an attempt to calculate generic section properties using shapely polygon grid masking
# @otterize
# class Profile(Configuration):
#     '''A profile takes an shapely polygon as a profile, possibly with an inner polygon to mask points and then determine section properties'''
#     profile = attr.ib(validator= attr.validators.instance_of( Polygon ) ) #required
#     Npts = attr.ib(default=1000)
    
#     grid_aspect_max = 2.0 #ratio of cells Dxi, will adjust dimensions with max Npts
#     _grid = None

#     @property
#     def box_extents(self):
#         return self.profile.bounds

#     @property
#     def dxdy(self):
#         '''returns dx,dy as constrained by max aspect ratio'''
#         minx, miny, maxx, maxy = self.box_extents
#         dx,dy = (maxx - minx)/Npts, (maxy- miny)/Npts
#         if (dx/dy) >= self.grid_aspect_max: #dx is limiting
#             dy = dx /self.grid_aspect_max
#             return dx,dy

#         if (dy/dx) >= self.grid_aspect_max: #dy is limiting
#             dx = dy / self.grid_aspect_max
#             return dx,dy

#         return dx,dy

#     @property
#     def grid(self):
#         if self._grid is None:
#             dx,dy = self.dxdy
#             minx, miny, maxx, maxy = self.box_extents
#             X = numpy.arange( minx , maxx+dx , dx )
#             Y = numpy.arange( miny , maxy+dy , dy )
#             self._grid = GX,GY = meshgrid(X,Y)
#         return self._grid


#     def shp_mask(self, shp, x, y, m=None):
#         """Use recursive sub-division of space and shapely contains method to create a raster mask on a regular grid.
#         :param shp : shapely's Polygon (or whatever with a "contains" method and intersects method)
#         :param m : mask to fill, optional (will be created otherwise)
#         """
#         rect = self.box_extents

#         if m is None:
#             m = np.zeros((y.size, x.size), dtype=bool)
                
#         if not shp.intersects(rect):
#             m[:] = False
        
#         elif shp.contains(rect):
#             m[:] = True
        
#         else:
#             k, l = m.shape
            
#             if k == 1 and l == 1:
#                 m[:] = shp.contains(Point(x[0], y[0]))
                
#             elif k == 1:
#                 m[:, :l//2] = shp_mask(shp, x[:l//2], y, m[:, :l//2])
#                 m[:, l//2:] = shp_mask(shp, x[l//2:], y, m[:, l//2:])
                
#             elif l == 1:
#                 m[:k//2] = shp_mask(shp, x, y[:k//2], m[:k//2])
#                 m[k//2:] = shp_mask(shp, x, y[k//2:], m[k//2:])
            
#             else:
#                 m[:k//2, :l//2] = shp_mask(shp, x[:l//2], y[:k//2], m[:k//2, :l//2])
#                 m[:k//2, l//2:] = shp_mask(shp, x[l//2:], y[:k//2], m[:k//2, l//2:])
#                 m[k//2:, :l//2] = shp_mask(shp, x[:l//2], y[k//2:], m[k//2:, :l//2])
#                 m[k//2:, l//2:] = shp_mask(shp, x[l//2:], y[k//2:], m[k//2:, l//2:])
            
#         return m
