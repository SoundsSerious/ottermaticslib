from contextlib import contextmanager
import attr



@attr.s
class Configuration(object):

    _temp_vars = None

    name = attr.ib(default='default')

    @contextmanager
    def difference(self,**kwargs):
        '''Change Variables Temporarilly'''
        _temp_vars = {}
            
        _temp_vars.update({arg: getattr(self,arg) for arg in kwargs.keys() if hasattr(self,arg)})

        bad_vars = set.difference(set(kwargs.keys()),set(_temp_vars.keys()))
        if bad_vars:
            print('Could Not Change {}'.format( ','.join(list(bad_vars ) )))

        try: #Change Variables To Input
            for arg,var in kwargs.items():
                setattr(self,arg,var)
            yield self
        finally:
            for arg in kwargs.keys():
                var = _temp_vars[arg]
                setattr(self,arg,var)

    @property
    def filename(self):
        return self.name.replace(' ','_')