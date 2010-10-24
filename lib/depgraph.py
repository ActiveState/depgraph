# Copyright (c) 2010 ActiveState Software Inc.

"""
    pypm.client.depgraph
    ~~~~~~~~~~~~~~~~~~~~

    An independent module containing the dependency resolution algorithm that
    is based on a graph ('depgraph') of requirements with support for
    setuptools-style 'extras' and version 'specs'.

    See pypm.client.installer.PyPMDepGraph for a PyPM-specific class that wraps
    this modules's DepGraph class.

    We intend to keep this module as pure (i.e., not relying on pypm code) as
    possible.
"""

import logging
import operator
from collections import namedtuple, defaultdict
from abc import ABCMeta, abstractmethod

from pkg_resources import Requirement


__version__ = '0.9'

LOG = logging.getLogger(__name__)


class RequirementNotFound(Exception):
    """No distribution found for the given requirement"""

    def __init__(self, requirement, required_by=None):
        self._requirement = requirement
        self._required_by = required_by
        msg = 'no distribution for "%s" found' % requirement
        if required_by:
            msg += '; required by "%s"' % required_by
        super(RequirementNotFound, self).__init__(msg)


# See `DepGraph` class below
class MarkMixin(object):
    """Mixin to mark packages for install/removal/change"""

    def __init__(self):
        # Meanings for Node(..., pkg, pkg1):
        #   (p, None)   -> Already installed
        #   (None, p)   -> Install
        #   (p1, p2)    -> upgrade/downgrade
        #   (p, sentinal_delete) -> Uninstall
        self.Node = namedtuple('Node', 'name pkg pkg1')
        self.sentinal_delete = object()
        self.nodes = {}
        
        # The order nodes were added, changed, removed when
        # add_requirement and/or remove_package is called.
        # This order will be used in `get_marks` which in turn gets used
        # to install/uninstall packages in correct order, so that when the
        # installation fails, packages are not left with broken
        # dependencies
        self._order_new = _Order()
        self._order_change = _Order()
        self._order_remove = _Order()

        # Meaning for edge[node1][node2] = [r1, r2, ...]
        #  node1 is "required by" node2 under r1, r2, ... requirements
        self.edges = defaultdict(lambda: defaultdict(list))

    def _mark_new_requirement(self, n1, n2, r):
        self.edges[n1][n2].append(r)

    def _mark_for_install(self, name, p, required_by=None, requirement=None):
        """Mark a new package for install"""
        assert name not in self.nodes
        node = self.nodes[name] = self.Node(
            name=name,
            pkg=None,
            pkg1=p)
        if required_by:
            self._mark_new_requirement(name, required_by, requirement)
            
        # mark order
        self._order_new.push(name)
        
        return node
        
    def _mark_for_change(self, name, p, required_by=None, requirement=None):
        """Mark an existing package for upgrade/downgrade
        
        If no change is effective, node.pkg1 will be None. The caller can check
        this.
        """
        assert name in self.nodes
        node = self.nodes[name]
        
        # if new package is of same version, revert it
        if p.version_key == node.pkg.version_key:
            p = None
            
        node = self.nodes[name] = self.Node(
            name=name,
            pkg=node.pkg,
            pkg1=p)
        if required_by:
            self._mark_new_requirement(name, required_by, requirement)
            
        # mark order
        self._order_change.push(name)
        
        return node
    
    def _mark_for_removal(self, name):
        """Mark the current package for removal
        
        Return the node *if* wasn't marked for removal *before*
        """
        assert name in self.nodes, '"%s" not in self.nodes' % name
        node = self.nodes[name]
        if node.pkg1 == self.sentinal_delete:
            return None
        assert node.pkg1 is None, \
            '"%s" was already marked for install/change' % name
        node = self.nodes[name] = self.Node(
            name=name,
            pkg=node.pkg,
            pkg1=self.sentinal_delete)
        
        # mark order
        self._order_remove.push(name)
        
        return node
        
    def get_marks(self):
        """Return packages that are marked
        
        Returns a structure similar to:
        
            return {
              'install': pkg, ...,
              'remove':  pkg, ...,
              'change':  (pkg1, pkg2), ...,
            }
        """
        d = defaultdict(list)
        for name, node in self.nodes.items():
            if node.pkg1:
                if id(node.pkg1) == id(self.sentinal_delete):
                    d['remove'].append(node.pkg)
                elif node.pkg:
                    d['change'].append((node.pkg, node.pkg1))
                else:
                    d['install'].append(node.pkg1)
                    
        # rearrange package lists in reverse order of dependencies
        def change_key(pair):
            p1, p2 = pair
            return p1.name
        self._order_new.rearrange_list(
            d['install'], key=operator.attrgetter('name'), reverse=True)
        self._order_change.rearrange_list(
            d['change'], key=change_key, reverse=True)
        self._order_remove.rearrange_list(
            d['remove'], key=operator.attrgetter('name'), reverse=False)
        return d
    
    def display(self):
        """Log a pretty display of the depgraph with marks if any"""
        LOG.info('DepGraph with %d nodes:-', len(self.nodes))
        LOG.info('Nodes:')
        LOG.info(wrapped(', '.join(self.nodes.keys()), prefix='\t'))
        LOG.info('Edges:')
        for n1, v in self.edges.items():
            n1_once = n1
            for n2, rl in v.items():
                LOG.info(wrapped('{0:18} <- {1:18} [{2}]'.format(
                    n1_once, n2, ', '.join([str(r) for r in rl])), prefix='\t'))
                n1_once = ' ' * len(n1_once)
                
        LOG.info('Marks:')
        marks = self.get_marks()
        for pkg in marks['install']:
            LOG.info('\t[+] %s', pkg.full_name)
        for pkg in marks['remove']:
            LOG.info('\t[-] %s', pkg.full_name)
        for p1, p2 in marks['change']:
            LOG.info('\t[c] %s -> %s', p1.full_name, p2.full_name)
            if p1.version_key > p2.version_key:
                # show reasonf for downgrade
                because = 'because '
                for name0, rl in self.edges[p1.name].items():
                    for r in rl:
                        LOG.info(wrapped('\t    {0}{1} requires {2}'.format(
                            because, name0, r)))
                        because = ' ' * len(because)  # reset
                        
    
class DepGraph(MarkMixin):
    """A dependency graph of requirements/packages
    
    You must inherit this class and define the two methods:
    
      - get_installed_distributions()
      - get_available_distributions(name)
      
    These methods must appropriately (as named) return a list of distribution
    objects with following attributes and methods:
    
      - .name                      : Distribution' canonical name
      - .version_key               : Comparable key of its version string
      - .get_requirements(extras)  : List of requirements (setuptools-style)
      
    See commmand.{install,uninstall} to see an example usage
    """
    __metaclass__ = ABCMeta
    
    def __init__(self):
        super(DepGraph, self).__init__()
        self._load_install_db()
        
    @abstractmethod
    def get_installed_distributions(self):
        """Return a list of installed distributions
        
        Multi-version distributions are not supported and the returned list
        will (and must) contain only one entry per distribution name
        """
        
    @abstractmethod
    def get_available_distributions(self, name):
        """Return a list of distributions under ``name`` available to install
        
        Typically this corresponds to what is available in PyPI or some custom
        repository. Multiple versions of the distribution may be available and
        they are returned in descending order of versions.
        """
        
    def has_package(self, name):
        return name in self.nodes
                    
    def remove_package(self, name, nodeps=False):
        node = self._mark_for_removal(name)
        if node and not nodeps:
            rdepends = self.edges[name].keys()
            for name1 in rdepends:
                self.remove_package(name1)
            
    def add_requirement(self, r, nodeps=False, parent=None):
        """Add a new requirement to the depgraph
        
        Requirements for this requirements are automatically added.
        
        Return True unless if this requirement (`r`) is already satisfied
        recursively.
        
        Raises RequirementNotFound if not distribution found matching r.
        """
        if not isinstance(r, Requirement):
            r = Requirement.parse(r)
        # print('add_requirement: %s, parent=%s' %( r, parent))
        name = req_name(r)
        ret = False
        
        to_satisfy = [r]
        node = self.nodes.get(name, None)
        if node:
            to_satisfy.extend(sum(self.edges[name].values(), []))
        
        releases = self.get_available_distributions(name)
        if not releases:
            raise RequirementNotFound(r, parent)
        
        satisfying_packages = [
            p for p in releases \
            if all([p.version in req for req in to_satisfy])]
        
        if not satisfying_packages:
            raise RequirementNotFound(req2str(*to_satisfy), parent)
            
        p = satisfying_packages[0]
        if node:
            # node_pkg is the package that will be installed; if nothing is to be
            # installed, then it is the one that is already installed.
            node_pkg = node.pkg1 or node.pkg
            assert node_pkg
            change = True
            
            if node_pkg.version_key == p.version_key:
                # TEST: 'pypm install --nodeps fabric' followed by
                #       'pypm install fabrics' should ideally install the deps now
                change = False
            elif node_pkg.version_key > p.version_key:
                # If installed package is of newer version, allow it only
                # if it satisfies all the requirements.
                # TEST:
                #   numpy-2 is installed, but repo has only 1.5
                #   'pypm install matpotlib' should NOT downgrade numpy-2
                # TEST:
                #   'pypm install numpy<1.999' should downgrade it, though
                for req in to_satisfy:
                    if node_pkg.version not in req:
                        break
                else:
                    # Already satisfied
                    change = False
                
            if change:
                # downgrade or upgrade
                node = self._mark_for_change(name, p, parent, r)
                if node.pkg1:
                    ret = True
                    
                    # TODO: adjust requirements
                    rl0 = tuple(node_pkg.get_requirements(r.extras))
                    rl1 = tuple(node.pkg1.get_requirements(r.extras))
                    if not nodeps and rl0 != rl1:
                        msg = (
                            ('need to implement requirements differing across versions;'
                             '\n    %s-%s [%s]\n -> %s-%s [%s]') % (
                                name, node_pkg.printable_version,
                                req2str(*rl0),
                                name, node.pkg1.printable_version,
                                req2str(*rl1)))
                        LOG.warn(msg)
            else:
                self._mark_new_requirement(name, parent, r)
        else:
            node = self._mark_for_install(name, p, parent, r)
            ret = True
            
        if not nodeps:
            pkg = node.pkg1 or node.pkg # check requirements even if the package is installed
            assert pkg
            for r in pkg.get_requirements(r.extras):
                ret = any([
                    ret,
                    self.add_requirement(
                        r, nodeps=nodeps, parent=node and node.name)])
                
        return ret

    def _load_install_db(self):
        """Load installed packages into the graph"""
        extra_requirements = set()
        ipackages = self.get_installed_distributions()
        
        # pass 1: add installed packages
        for ipkg in ipackages:
            name = ipkg.name
            self.nodes[name] = self.Node(name=name, pkg=ipkg, pkg1=None)
            
        # pass 2: add their requirements (if the req *is* installed)
        for name, node in self.nodes.items():
            ipkg = node.pkg
            for r in ipkg.get_requirements():
                rname = req_name(r)
                # TEST: if 'r' is not installed
                if self.has_package(rname):
                    self._mark_new_requirement(rname, name, r)
                    if r.extras: # defer extra handling in pass 3
                        for e in r.extras:
                            extra_requirements.add((rname, e, name))
                
        # pass 3: handle 'extras'
        for n1, e, n2 in extra_requirements:
            for r in self.nodes[n1].pkg.get_requirements([e], exclude_default=True):
                # Add an "indirect" requirement edge
                self._mark_new_requirement(req_name(r), n2, r)
                

def req_name(r):
    return r.project_name.lower()
    

def req2str(*reqlist):
    return ', '.join([str(r) for r in reqlist])


class _Order:
    """Remember element order, and later rearrange the given list in the same order"""
    
    def __init__(self):
        self._elements = []
        self._elements_set = set()
        
    def push(self, element):
        """Add the given element in last position
        
        If the element already exists, change its position as a final element"""
        if element in self._elements_set:
            self._elements.remove(element)
            self._elements_set.remove(set)
        self._elements.append(element)
        self._elements_set.add(element)

    def rearrange_list(self, lst, key, reverse=False):
        """Rearrange the elements of the given list in current order
        
        Elements not already added by `push` are pushed to the end of the list.
        """
        indices = dict([(e, i) for (i, e) in enumerate(self._elements)])
        lst.sort(key=lambda e: indices.get(key(e), 99999), reverse=reverse)


# copied from pypm/common/util.py
import textwrap
def wrapped(txt, prefix='', **options):
    """Return wrapped text suitable for printing to terminal"""
    MAX_WIDTH=70 # textwrap.wrap's default
    return '\n'.join([
            '{0}{1}'.format(prefix, line)
            for line in textwrap.wrap(
                txt, width=MAX_WIDTH-len(prefix), **options)])


if __name__ == '__main__':
    # Just a demonstrating example
    import logging
    logging.basicConfig(level=logging.INFO)
    import pkg_resources
    class ExampleDepGraph(DepGraph):
        def get_installed_distributions(self):
            # This just returns the installed packages
            return [
                Distribution('fabric', '0.9.1', {'': ['pycrypto']}),
                Distribution('pycrypto', '2.1', {'': []}),
                Distribution('virtualenv', '1.4.0', {'': []}),
            ]
        def get_available_distributions(self, name):
            # typically, this should return all distributions in PyPI matching
            # `name'. Note that the returned distribution objects must also
            # contain list of requirements (see get_requirements below)
            distributions = dict(
                fabric = [
                    Distribution('fabric', '0.9.2', {'': ['pycrypto<=2.1',
                                                          'paramiko']}),
                    Distribution('fabric', '0.9.1', {'': ['pycrypto']}),
                ],
                paramiko = [
                    Distribution('paramiko', '0.9', {'': ['pycrypto']}),
                ],
                pycrypto = [
                    Distribution('pycrypto', '2.3', {'': []}),
                    Distribution('pycrypto', '2.1', {'': []}),
                ],
            )
            if name not in distributions:
                raise NotImpementedError()
            return distributions[name]
    
    _Distribution = namedtuple('_Distribution', 'name version install_requires')
    class Distribution(_Distribution):
        @property
        def printable_version(self):
            return self.version
        @property
        def full_name(self):
            return self.name + '-' + self.version
        @property
        def version_key(self):
            return pkg_resources.parse_version(self.version)
        def get_requirements(self, with_extras=None):
            # this returns list of requirements (optionally with extras)
            extras = ('',) + (with_extras or ())
            extras = set(extras)
            for extra in extras:
                for rs in self.install_requires[extra]:
                    yield pkg_resources.Requirement.parse(rs)
            
    graph = ExampleDepGraph()
    print('Current install state:-')
    graph.display()
    print('State after marking "fabric" to be installed:')
    graph.add_requirement('fabric')
    graph.display()
    
