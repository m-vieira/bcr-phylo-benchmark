#! /bin/env python

import scipy, warnings, random
from scipy.misc import logsumexp
from scipy.optimize import minimize, check_grad

import matplotlib
matplotlib.use('PDF')
from matplotlib import pyplot as plt
from matplotlib import rc, ticker
from scipy.stats import probplot
from ete3 import Tree, NodeStyle, TreeStyle, TextFace, add_face_to_node, CircleFace, faces, AttrFace, nexml
from Bio.Seq import Seq
from Bio.Alphabet import generic_dna


"""
This module contains classes for simulation and inference for a binary branching process with mutation
in which the tree is collapsed to nodes that count the number of clonal leaves of each type
"""

class LeavesAndClades():
    """
    This is a base class for simulating, and computing likelihood for, a binary infinite type branching
    process with branching probability p, mutation probability q, and we collapse mutant clades off the
    root type and consider just the number of clone leaves, c, and mutant clades, m.

      /\            
     /\ ^          (3)
      /\     ==>   / \\
       /\\
        ^
    """
    def __init__(self, p=None, q=None, c=None, m=None):
        """initialize with branching probability p and mutation probability q, both in the unit interval"""
        if p is not None or q is not None:
            if not (0 <= p <= 1 and 0 <= q <= 1):
                raise ValueError('p and q must be in the unit interval')
        self._p = p
        self._q = q
        if c is not None or m is not None:
            if not (c >= 0) and (m >= 0) and (c+m > 0):
                raise ValueError('c and m must be nonnegative integers summing greater than zero')
            self._c = c
            self._m = m

    def simulate(self):
        """simulate the number of clone leaves and mutant clades off a root node"""
        if self._p>=.5:
            warnings.warn('p >= .5 is not subcritical, tree simulations not garanteed to terminate')
        if self._p is None or self._q is None:
            raise ValueError('p and q parameters must be defined for simulation\n')

        # let's track the tree in breadth first order, listing number clone and mutant descendants of each node
        # mutant clades terminate in this view
        cumsum_clones = 0
        len_tree = 0
        self._c = 0
        self._m = 0
        # while termination condition not met
        while cumsum_clones > len_tree - 1:
            if random.random() < self._p:
                mutants = sum(random.random() < self._q for child in range(2))
                clones = 2 - mutants 
                self._m += mutants
            else:
                mutants = 0
                clones = 0
                self._c += 1
            cumsum_clones += clones
            len_tree += 1
        assert cumsum_clones == len_tree - 1

    f_hash = {} # <--- class variable for hashing calls to the following function
    def f(self, p, q, sign=1):
        """
        Probability of getting c leaves that are clones of the root and m mutant clades off
        the root line, given branching probability p and mutation probability q 
        Also returns gradient wrt (p, q)
        Computed by dynamic programming
        """
        c, m = self._c, self._m
        if (p, q, c, m) not in LeavesAndClades.f_hash:
            if c==m==0 or (c==0 and m==1):
                f_result = 0
                dfdp_result = 0
                dfdq_result = 0
            elif c==1 and m==0:
                f_result = 1-p
                dfdp_result = -1
                dfdq_result = 0
            elif c==0 and m==2:
                f_result = p*q**2
                dfdp_result = q**2
                dfdq_result = 2*p*q
            else:
                if m >= 1:
                    neighbor = LeavesAndClades(p=p, q=q, c=c, m=m-1)
                    neighbor_f, (neighbor_dfdp, neighbor_dfdq) = neighbor.f(p, q)
                    f_result = 2*p*q*(1-q)*neighbor_f
                    dfdp_result =   2*q*(1-q) * neighbor_f + \
                                  2*p*q*(1-q) * neighbor_dfdp 
                    dfdq_result = (2*p - 4*p*q) * neighbor_f + \
                                   2*p*q*(1-q)  * neighbor_dfdq
                else:
                    f_result = 0.
                    dfdp_result = 0.
                    dfdq_result = 0.
                for cx in range(c+1):
                    for mx in range(m+1):
                        if (not (cx==0 and mx==0)) and (not (cx==c and mx==m)):
                            neighbor1 = LeavesAndClades(p=p, q=q, c=cx, m=mx)
                            neighbor2 = LeavesAndClades(p=p, q=q, c=c-cx, m=m-mx)
                            neighbor1_f, (neighbor1_dfdp, neighbor1_dfdq) = neighbor1.f(p, q)
                            neighbor2_f, (neighbor2_dfdp, neighbor2_dfdq) = neighbor2.f(p, q)
                            f_result += p*(1-q)**2*neighbor1_f*neighbor2_f
                            dfdp_result +=   (1-q)**2 * neighbor1_f    * neighbor2_f + \
                                           p*(1-q)**2 * neighbor1_dfdp * neighbor2_f + \
                                           p*(1-q)**2 * neighbor1_f    * neighbor2_dfdp
                            dfdq_result += -2*p*(1-q) * neighbor1_f    * neighbor2_f + \
                                           p*(1-q)**2 * neighbor1_dfdq * neighbor2_f + \
                                           p*(1-q)**2 * neighbor1_f    * neighbor2_dfdq
            LeavesAndClades.f_hash[(p, q, c, m)] = (f_result, scipy.array([dfdp_result, dfdq_result]))
        return LeavesAndClades.f_hash[(p, q, c, m)]

    def get(self, param_name=None):
        """
        return a dictionary of member variables, or a single parameter indicated by param_name
        param_name may equal 'p', 'q', or 'tree', or None.
        """
        if param_name is None:
            return {'p':self._p, 'q':self._q, 'c':self._c, 'm':self._m}
        elif param_name is 'p':
            return self._p
        elif param_name is 'q':
            return self._q
        elif param_name is 'c':
            return self._c
        elif param_name is 'm':
            return self._m
        else:
            raise ValueError("param_name may equal 'p', 'q', 'c', 'm', or None.")


class CollapsedTree(LeavesAndClades):
    """
    Here's a derived class for a collapsed tree, where we recurse into the mutant clades
          (4)
         / | \\
       (3)(1)(2)
           |   \\
          (2)  (1)
    """
    def __init__(self, p=None, q=None, tree=None):
        """
        For intialization, either p and q or tree (or all three) must be provided
        p: branching probability
        q: mutation probability
        tree: ete tree with frequency node feature. If uncollapsed, it will be collapsed
        """
        #if p is None and q is None and tree is None:
        #    raise ValueError('either p and q or tree (or all three) must be provided')
        LeavesAndClades.__init__(self, p=p, q=q)
        if tree is not None:
            self._tree = tree.copy()
            if 0 in (node.dist for node in tree.iter_descendants()):
                # iterate over the tree below root and collapse edges of zero length
                for node in self._tree.get_descendants():
                    if node.dist == 0:
                        node.up.frequency += node.frequency
                        node.delete(prevent_nondicotomic=False)
            assert sum(node.frequency for node in tree.traverse()) == sum(node.frequency for node in self._tree.traverse())
            if len(set([node.sequence for node in self._tree.traverse()])) != sum(1 for _ in self._tree.traverse()):
                warnings.warn('repeated sequences in collapsed tree, possible backmutation', RuntimeWarning)
        else:
            self._tree = tree


    def phi(self, x, n):
        """
        The nth composition of the generating function of the offspring distribution
        This is the generating function of the total number of (uncollapsed) nodes in the nth generation
        Note: since collapsed tree simulations don't currently capture fine structure, this is of limited use
        """
        if n == 1:
            return (1-self._p) + self._p*x**2
        elif n > 1:
            return phi(x, n-1)
        else:
            raise ValueError('n must be a natural number')

    def sf(self, n):
        """
        The survival function of the extinction time, n (integer number of generations), of the uncollapsed tree
        This is computed analytically in terms of the generating funtion, phi, of the offsprint distribution
        Note: since collapsed tree simulations don't currently capture fine structure, this is of limited use
        """
        return 1 - phi(self, 0, n)

    def l(self, (p, q), sign=1):
        """
        log likelihood of p and q, conditioned on collapsed tree, and its gradient wrt (p, q)
        optional parameter sign must be 1 or -1, with the latter useful for MLE by minimization
        """
        if self._tree is None:
            raise ValueError('tree data must be defined to compute likelihood')
        if sign not in (-1, 1):
            raise ValueError('sign must be 1 or -1')
        leaves_and_clades_list = [LeavesAndClades(c=node.frequency, m=len(node.children)) for node in self._tree.traverse()]
        if leaves_and_clades_list[0]._c == 0 and leaves_and_clades_list[0]._m == 1 and leaves_and_clades_list[0].f(p, q)[0] == 0:
            print 'WARNING: unifurcation from root not possible under current model. This node will be ommitted from likelihood calculation'
            leaves_and_clades_list = leaves_and_clades_list[1:]
        f_data = [leaves_and_clades.f(p, q) for leaves_and_clades in leaves_and_clades_list]
        # extract vector of function values and gradient components
        fs = scipy.array([x[0] for x in f_data])
        dfdps = scipy.array([x[1][0] for x in f_data])
        dfdqs = scipy.array([x[1][1] for x in f_data])
        return sign*scipy.log(fs).sum(), sign*scipy.array([(dfdps/fs).sum(), (dfdqs/fs).sum()])

    def mle(self, **kwargs):
        """
        Maximum likelihood estimate for p and q given tree
        updates p and q if not None
        returns optimization result
        """
        # random initalization
        x_0 = (random.random(), random.random())
        #x_0 = (.5, .5)
        bounds = ((.01, .99), (.001, .999))
        kwargs['sign'] = -1
        #print check_grad(lambda x: self.l(x, **kwargs)[0], lambda x: self.l(x, **kwargs)[1], (.4, .5))
        result = minimize(lambda x: self.l(x, **kwargs), x0=x_0, jac=True, method='L-BFGS-B', options={'ftol':1e-10}, bounds=bounds)
        # update p and q if None and optimization successful
        if not result.success:
            warnings.warn('optimization not sucessful, '+result.message, RuntimeWarning)
        elif self._p is None and self._q is None:
            self._p, self._q = result.x
        return result

    def simulate(self):
        """
        simulate a collapsed tree given p and q
        replaces existing tree data member with simulation result, and returns self
        """
        if self._p is None or self._q is None:
            raise ValueError('p and q parameters must be defined for simulation')

        # initiate by running a LeavesAndClades simulation to get the number of clones and mutants
        # in the root node of the collapsed tree
        LeavesAndClades.simulate(self)
        self._tree = Tree()
        self._tree.add_feature('frequency', self._c)
        if self._m == 0:
            return self
        for _ in range(self._m):
            # oooo, recursion
            self._tree.add_child(CollapsedTree(p=self._p, q=self._q).simulate()._tree)

        return self
                
    def get(self, param_name=None):
        """
        return a dictionary of member variables, or a single parameter indicated by param_name
        param_name may equal 'p', 'q', or 'tree', or None.
        """
        if param_name is None:
            return {'p':self._p, 'q':self._q, 'tree':self._tree}
        elif param_name is 'p':
            return self._p
        elif param_name is 'q':
            return self._q
        elif param_name is 'tree':
            return self._tree
        else:
            raise ValueError("param_name may equal 'p', 'q', or 'tree', or None.")

    def __str__(self):
        """return a string representation for printing"""
        return 'p = %f, q = %f\ntree:\n' % (self._p, self._q) + str(self._tree)

    def render(self, plot_file, colormap=None):
        """render to image file, filetype inferred from suffix, png for color images"""
        for node in self._tree.traverse():
            nstyle = NodeStyle()
            if node.frequency == 0:
                nstyle['size'] = 5
                nstyle['fgcolor'] = 'grey'
            else:
                nstyle['size'] = 3*2*scipy.sqrt(scipy.pi*node.frequency)
                if colormap is not None and node.name in colormap:
                    nstyle['fgcolor'] = colormap[node.name]
                else:
                    nstyle['fgcolor'] = 'black'
            if node.up is not None:
                if set(node.sequence.upper()) == set('ACGT'):
                    nonsyn = hamming_distance(Seq(node.sequence, generic_dna).translate(), Seq(node.up.sequence, generic_dna).translate())
                    if nonsyn > 0:
                        nstyle['hz_line_color'] = 'black'
                        nstyle["hz_line_width"] = nonsyn
                    else:
                        nstyle["hz_line_type"] = 1
                    if '*' in Seq(node.sequence, generic_dna).translate():
                        nstyle['bgcolor'] = 'red'

            node.set_style(nstyle)

        ts = TreeStyle()
        ts.show_leaf_name = False
        ts.rotation = 90
        def my_layout(node):
            if node.frequency > 1:
                N = TextFace(node.frequency, fsize=14, fgcolor='black')
                N.rotation = -90
                faces.add_face_to_node(N, node, 0, position='branch-top')
        ts.layout_fn = my_layout
        self._tree.render(plot_file, tree_style=ts)

    def write(self, file_name):
        self._tree.write(features=[], outfile=file_name)

        
class CollapsedForest(CollapsedTree):
    """
    simply a set of CollapsedTrees, with the same p and q parameters
          (4)          (3)
         / | \\         / \\
       (3)(1)(2)     (1) (2)
           |   \\  ,          , ...
          (2)  (1)
    """
    def __init__(self, p=None, q=None, n_trees=None, forest=None):
        """
        in addition to p and q, we need number of trees
        can also intialize with forest, a list of trees, each same format as tree member of CollapsedTree
        """
        CollapsedTree.__init__(self, p=p, q=q)
        if forest is None and p is None and q is None:
            raise ValueError('either p and q or forest (or all three) must be provided')
        if forest is not None:
            if len(forest) == 0:
                raise ValueError('passed empty tree list')
            if n_trees is not None and len(forest) != n_trees:
                raise ValueError('n_trees not consistent with forest')
            self._forest = forest
        if n_trees is not None and n_trees < 1:
            raise ValueError('number of trees must be at least one')
        if n_trees is None and forest is not None:
            self._n_trees = len(forest)
        self._n_trees = n_trees
        
    def simulate(self):
        """
        simulate a forest of collapsed trees given p and q and number of trees
        replaces existing forest data member with simulation result, and returns self
        """
        if self._p is None or self._q is None or self._n_trees is None:
            raise ValueError('p, q, and n_trees parameters must be defined for simulation')
        tree = CollapsedTree(self._p, self._q)
        self._forest = [tree.simulate().get('tree') for x in range(self._n_trees)]
        return self

    def l(self, (p, q), sign=1, Vlad_sum=False):
        """
        likelihood of (p, q), given forest, and it's gradient wrt (p, q)
        optional parameter sign must be 1 or -1, with the latter useful for MLE by minimization
        if optional parameter Vlad_sum is true, we're doing the Vlad sum for estimating p, q for
        as set of parsimony trees
        """
        if self._forest is None:
            raise ValueError('forest data must be defined to compute likelihood')
        if sign not in (-1, 1):
            raise ValueError('sign must be 1 or -1')
        # since the l method on the CollapsedTree class returns l and grad_l...
        if Vlad_sum:
            terms = [CollapsedTree(tree=tree).l((p, q)) for tree in self._forest]
            sumexp = scipy.exp([x[0] for x in terms]).sum()
            #sumexp = scipy.exp(logsumexp([x[0] for x in terms]))
            #assert sumexp != 0
            #thing1 = [x[0]+scipy.log(x[1][0]) for x in terms if x[1][0] > 0]
            #thing2 = [x[0]+scipy.log(x[1][1]) for x in terms if x[1][1] > 0]
            #thing3 = [x[0] for x in terms]
            #return sign*(-scipy.log(len(terms)) + logsumexp(thing3)), \
            #       sign*scipy.array([scipy.exp(logsumexp(thing1) + logsumexp(thing3)), scipy.exp(logsumexp(thing2) + logsumexp(thing3))])
            return sign*(-scipy.log(len(terms)) + logsumexp([x[0] for x in terms])), \
                   sign*scipy.array([sum(scipy.exp(x[0])*x[1][0] for x in terms)/sumexp,
                                     sum(scipy.exp(x[0])*x[1][1] for x in terms)/sumexp])
        else:
            terms = [CollapsedTree(tree=tree).l((p, q), sign=sign) for tree in self._forest]
            return sum(x[0] for x in terms), scipy.array([sum(x[1][0] for x in terms), sum(x[1][1] for x in terms)])

    # NOTE: we get mle() method for free by inheritance/polymorphism magic

    def get(self, param_name=None):
        """
        return a dictionary of member variables (None argument), or a single parameter indicated by param_name
        param_name may equal 'p', 'q', 'n_trees', or 'forest'.
        """
        if param_name is None:
            return {'p':self._p, 'q':self._q, 'n_trees':self._n_trees, 'forest':self._forest}
        elif param_name is 'p':
            return self._p
        elif param_name is 'q':
            return self._q
        elif param_name is 'n_trees':
            return self._n_trees
        elif param_name is 'forest':
            return self._forest
        else:
            raise ValueError("param_name may equal 'p', 'q', or 'tree', or None.")

    def __str__(self):
        """return a string representation for printing"""
        return ('p = %f, q = %f, n_trees = %d\n'+
                '\n'.join([str(tree) for tree in self._forest])) % (self._p, self._q, self._n_trees)

        
def test(p, q, n, plot_file):
    """
    checks likelihood against a by-hand calculation for a simple tree, simulates a forest, computes MLE parameters, and plots some sanity check figures to plot_file
    command line arguments are p, q, number of trees to simulate, and plot file name
    """

    if plot_file[-4:] != '.pdf':
        plot_file += '.pdf'

    print 'Let''s check our likelihood against a by-hand calculation for the following simple tree'
    tree = CollapsedTree(tree=[(2,1), (1,0)])
    print '    T =', str(tree.get('tree'))
    print '    Summing the probabilities of the two possible fine structures, we have'
    print '    Pr(T) = 6 p^2 (1-p)^3 q (1-q)^3 =', 6*p**2*(1-p)**3*q*(1-q)**3
    print '    Now, our dynamic programming algorithm gives'
    print '    Pr(T) =', scipy.exp(tree.l((p, q))[0])
    print ''

    print 'Simulating a forest of %d trees' % n
    forest = CollapsedForest(p, q, n)
    print '    true parameters: p = %f, q = %f' % (p, q)
    forest.simulate()

    # total leaf counts
    total_data = sorted([sum(x[0] for x in tree) for tree in forest.get('forest')])
    max_total = max(total_data)
    len_total = len(total_data)

    totals, freq, log_prob = zip(*[(x, total_data.count(x), CollapsedTree(tree=[(x, 0)]).l((p, 0))[0]) for x in range(1, max_total+1)])
    theoretical_cdf = scipy.cumsum(scipy.exp(log_prob))
    empirical_cdf = scipy.cumsum(freq)/float(len_total)

    fig = plt.figure()
    fig.set_tight_layout(True)
    plt.rc('text', usetex=True)

    # plot the empirical and theoretical distribution of total leaf counts

    ax = fig.add_subplot(2,2,1)
    ax.plot(totals, scipy.exp(log_prob), 'ko', markerfacecolor='none', alpha=.5, label='theoretical PMF')
    ax.plot(totals, scipy.array(freq)/float(len_total), 'k.', label='empirical PMF')
    ax.legend(numpoints=1, loc=1, fontsize='small')
    ax.set_xlabel('total leaves')
    ax.set_ylabel('$\Pr($total leaves$)$')
    ax.set_ylim([0, 1.1])
    #ax.set_xscale('log')
    #ax.set_yscale('symlog')

# uncomment this if you want the CDF
#    ax = fig.add_subplot(2,2,2)
#    ax.plot(totals, theoretical_cdf, 'ko', markerfacecolor='none', alpha=.5, label='theoretical CDF')
#    ax.plot(totals, empirical_cdf, 'k.', label='empirical CDF')
#    ax.legend(numpoints=1, loc=4, fontsize='small')
#    ax.set_xlabel('number of leaves')
#    ax.set_ylim([0, 1.1])


    empirical_quantiles = []
    theoretical_quantiles = []
    for x in total_data:
        empirical_quantiles.append(sum(y <= x for y in total_data)/float(len_total))
        theoretical_quantiles.append(scipy.sum(scipy.exp([CollapsedTree(tree=[(y, 0)]).l((p, 0))[0] for y in range(1, x+1)])))

    ax = fig.add_subplot(2,2,2)
    ax.plot(theoretical_quantiles, empirical_quantiles, 'ko', alpha=.1)
    ax.plot([0, 1], [0, 1], 'k')
    ax.set_title('total leaves')
    ax.set_xlabel('theoretical quantiles')
    ax.set_ylabel('empirical quantiles')
    ax.set_xlim([0, 1])
    ax.set_ylim([0, 1])
    ax.set_aspect('equal')

    mle = forest.mle().x
    print '    MLE parameters:  p = %f, q = %f' % tuple(mle.tolist())

    # plot the 2-norm of the difference between the gradient and its finite difference approximation
    print 'computing plot data...'
    X, Y = scipy.mgrid[slice(.05, 1, .05),
                       slice(.05, 1, .05)]
    Z = scipy.zeros((X.shape[0], X.shape[1]))
    for i in range(Z.shape[0]):
        for j in range(Z.shape[1]):
            Z[i, j] = check_grad(lambda x: forest.l(x)[0], lambda x: forest.l(x)[1], (X[i, j], Y[i, j]))

    print 'done'
    ax = fig.add_subplot(2,2,3)
    ax.set_title(r'$||\nabla \ell(p, q) - \Delta \ell(p, q)||_2$')
    im = ax.contourf(X, Y, Z, locator=ticker.LogLocator(), cmap='Greys')
    ax.set_xlabel(r'$p$')
    ax.set_ylabel(r'$q$')
    ax.set_aspect('equal')
    fig.colorbar(im, ax=ax)


    # plot likelihood surface, with true and MLE parameters shown
    X, Y = scipy.mgrid[slice(.02, 1, .02),
                       slice(.02, 1, .02)]
    Z = scipy.zeros((X.shape[0], X.shape[1]))
    for i in range(Z.shape[0]):
        for j in range(Z.shape[1]):
            l, grad_l = forest.l((X[i, j], Y[i, j]))
            z = l
            Z[i, j] = z
    ax = fig.add_subplot(2,2,4)
    ax.set_title(r'$\ell(p, q)$')
    contour = ax.contour(X, Y, Z, colors='k', label='likelihood contours')
    for c in contour.collections:
        c.set_linestyle('solid')

    ax.clabel(contour, fontsize=9, inline=1)
    ax.plot([p], [q], 'k+', label='true parameters')
    ax.plot(mle[0], mle[1], 'ko', markerfacecolor='none', label='MLE parameters')
    ax.set_xlabel(r'$p$')
    ax.set_ylabel(r'$q$')
    ax.set_aspect('equal')
    ax.legend(numpoints = 1, fontsize='small')

    plt.savefig(plot_file)
    print 'plot saved to', plot_file

def hamming_distance(seq1, seq2):
    """Hamming distance between two sequences of equal length"""
    return sum(x != y for x, y in zip(seq1, seq2))


def phylip_parse(phylip_outfile, germline=None):
    """parse phylip outfile and return ete trees"""
    # parse phylip outfile
    outfiledat = [block.split('\n\n\n')[0].split('\n\n') for block in open(phylip_outfile, 'r').read().split('From    To     Any Steps?    State at upper node')[1:]]

    # ete trees
    trees = []
    for i, tree in enumerate(outfiledat):
        tree_sequence_dict = {}
        parent_dict = {}
        names = []
        for j, block in enumerate(tree):
            if j == 0:
                for line in block.split('\n'):
                    fields = line.split()
                    if len(fields) == 0:
                        continue
                    name = fields[1]
                    names.append(name)
                    if fields[0] == 'root':
                        seq = ''.join(fields[2:])
                        parent = None
                    else:
                        seq = ''.join(fields[3:])
                        parent = fields[0]
                    tree_sequence_dict[name] = seq
                    parent_dict[name] = parent
            else:
                for line in block.split('\n'):
                    fields = line.split()
                    name = fields[1]
                    if fields[0] == 'root':
                        seq = ''.join(fields[2:])
                    else:
                        seq = ''.join(fields[3:])
                    tree_sequence_dict[name] += seq

        # if integer branch (not weird ambiguous chars)
        if set(''.join([tree_sequence_dict[name] for name in names])) == set('ACGT'):
            #nodes = dict([(name, Tree(name=(name, tree_sequence_dict[name]), dist=hamming_distance(tree_sequence_dict[name], tree_sequence_dict[parent_dict[name]]) if parent_dict[name] is not None else None)) for name in names])
            nodes = {}
            for name in names:
                node = Tree(name=name, dist=hamming_distance(tree_sequence_dict[name], tree_sequence_dict[parent_dict[name]]) if parent_dict[name] is not None else None)
                node.add_feature('sequence', tree_sequence_dict[node.name])
                if node.name == germline:
                    node.add_feature('frequency', 0)
                elif '_' in node.name:
                    node.add_feature('frequency', int(node.name.split('_')[-1]))
                    node.name = '_'.join(node.name.split('_')[:-1])
                else:
                    node.add_feature('frequency', 0)
                nodes[name] = node
            tree = nodes[names[0]] # GL is first
            for name in parent_dict:
                if parent_dict[name] is not None:
                    nodes[parent_dict[name]].add_child(nodes[name])
            # reroot on germline
            if germline is not None:
                assert len(nodes[germline].children) == 0
                assert nodes[germline] in tree.children
                tree.remove_child(nodes[germline])
                nodes[germline].add_child(tree)
                tree.dist = nodes[germline].dist
                tree = nodes[germline]
                tree.dist = 0

            # assert branch lengths make sense
            for node in tree.iter_descendants():
                assert node.dist == hamming_distance(node.sequence, node.up.sequence)

            trees.append(tree)

    return trees


def main():
    """if "--test" option is passed, run the test suite, else load phylip file and do MLEs for each tree"""
    import sys, argparse
    from collections import Counter

    parser = argparse.ArgumentParser(description='multitype tree modeling')
    parser.add_argument('--test', action='store_true', default=False, help='run tests on library functions')
    parser.add_argument('--p', type=float, default=.4, help='branching probability for test mode')
    parser.add_argument('--q', type=float, default=.5, help='mutation probability for test mode')
    parser.add_argument('--n', type=int, default=100, help='forest size for test mode')
    parser.add_argument('--plot_file', type=str, default='foo.pdf', help='output file for plots from test mode')
    parser.add_argument('--germline', type=str, default=None, help='name of germline sequence (outgroup root)')
    parser.add_argument('--phylipfile', type=str, help='dnapars outfile (verbose output with sequences at each site)')
    parser.add_argument('--colormap', type=str, default=None, help='optional sequence-->color mappings')
    args = parser.parse_args()

    if args.test:
        test(args.p, args.q, args.n, args.plot_file)
        return

    if args.colormap is not None:
        colormap = {}
        for line in open(args.colormap, 'r'):
            sequence, color = line.rstrip().split()
            colormap[sequence.upper()] = color
        #colormap = {sequence:color for line in open(args.colormap, 'r') for sequence, color in line.rstrip().split()}

    trees = phylip_parse(args.phylipfile, args.germline)
    n_trees = len(trees)

    print 'number of trees with integer branch lengths:', n_trees

    # now we need to get collapsed trees
    collapsed_trees = []
    parsimony_scores = []
    for tree_i, tree in enumerate(trees):
        collapsed_tree = CollapsedTree(tree=tree)
        collapsed_trees.append(collapsed_tree)
        parsimony_scores.append(sum(node.dist for node in tree.iter_descendants()))

        collapsed_tree.render(args.plot_file+'.'+str(tree_i+1)+'.png', args.colormap)
        collapsed_tree.write(args.plot_file+'.'+str(tree_i+1)+'.newick')

    # fit p and q using all trees
    result = CollapsedForest(forest=[collapsed_tree.get('tree') for collapsed_tree in collapsed_trees]).mle(Vlad_sum=True)
    assert result.success
    print 'p = %f, q = %f' % tuple(result.x)

    print_data = []
    for i, collapsed_tree in enumerate(collapsed_trees):
        l = collapsed_tree.l(result.x)[0]
        totals = sum(node.frequency for node in collapsed_tree._tree.traverse())
        alleles = len(collapsed_tree.get('tree'))
        print_data.append((i+1, totals, alleles, parsimony_scores[i], l))

    print 'tree\ttotals\talleles\tparsimony\tlogLikelihood'
    for x in sorted(print_data, key=lambda x: (-x[-1], x[0])):
        print '\t'.join(map(str, x))
        sys.stdout.flush()

    plt.figure()
    cs = scipy.arange(11)
    ms = scipy.arange(20)
    colors=plt.cm.rainbow(scipy.linspace(0,1,len(cs)))
    for i, c in enumerate(cs):
        dat = scipy.array([LeavesAndClades(c=c, m=m).f(*result.x)[0] for m in ms])
        dat = dat/dat.sum()
        plt.plot(ms, dat, 'o--', alpha=.5, color=colors[i], label=r'$c = %d$' % c) 
    plt.xlabel(r'$m$')
    plt.ylabel(r'$\mathbb{P}\left(M=m\mid C=c\right)$')
    plt.legend(numpoints=1)
    plt.savefig(args.plot_file+'.diversification.pdf')

if __name__ == "__main__":
    main()




