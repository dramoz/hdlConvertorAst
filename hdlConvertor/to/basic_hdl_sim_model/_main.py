from itertools import chain

from hdlConvertor.hdlAst import HdlVariableDef, HdlCall, HdlBuiltinFn,\
    HdlDirection, HdlName, HdlComponentInst, HdlEnumDef, HdlClassDef
from hdlConvertor.to.basic_hdl_sim_model.stm import ToBasicHdlSimModelStm
from hdlConvertor.to.hdlUtils import Indent, iter_with_last
from hdlConvertor.to.basic_hdl_sim_model.utils import sensitivityByOp
from hdlConvertor.hdlAst._statements import ALL_STATEMENT_CLASSES
from hdlConvertor.hdlAst._expr import HdlTypeType


DEFAULT_IMPORTS = """\
from pyMathBitPrecise.array3t import Array3t, Array3val
from pyMathBitPrecise.bits3t import Bits3t, Bits3val
from pyMathBitPrecise.enum3t import define_Enum3t

from pycocotb.basic_hdl_simulator.model import BasicRtlSimModel
from pycocotb.basic_hdl_simulator.model_utils import sensitivity, connectSimPort
from pycocotb.basic_hdl_simulator.proxy import BasicRtlSimProxy
from pycocotb.basic_hdl_simulator.sim_utils import sim_eval_cond
"""


class ToBasicHdlSimModel(ToBasicHdlSimModelStm):
    """
    Convert hdlObject AST to BasicHdlSimModel
    (Python simulation model used by pycocotb simulator)

    :ivar ~.module_path_prefix: if None serialized output will be in a single file
        and no imports of components are produced, else
    :type ~.module_path_prefix: Optional[str]
    :type stm_outputs: Optional[Dict[iHdlStm, List[str]]]
    :attention: if you want to serialize a module definitions you need to set stm_outputs
    """
    ALL_STATEMENT_CLASSES = ALL_STATEMENT_CLASSES

    def __init__(self, out_stream):
        """
        :type out_stream: StringIO
        """
        ToBasicHdlSimModelStm.__init__(self, out_stream)
        self.module_path_prefix = None
        self.add_imports = True
        self.stm_outputs = None

    def visit_doc(self, obj):
        return super(ToBasicHdlSimModel, self).visit_doc(obj, "#")

    def visit_component_imports(self, components):
        w = self.out.write
        prefix = self.module_path_prefix
        if prefix is not None:
            for c in components:
                n = c.module_name
                with Indent(self.out):
                    w('from %s.%s import %s\n' % (prefix, n, n))

    def split_HdlModuleDefObjs(self, objs):
        """
        :type mod: List[iHdlObj]
        """
        types = []
        variables = []
        processes = []
        components = []
        obj_type_containers = {
            HdlClassDef: types,
            HdlEnumDef: types,
            HdlVariableDef: variables,
            HdlComponentInst: components,
        }
        for stmCls in self.ALL_STATEMENT_CLASSES:
            obj_type_containers[stmCls] = processes
        for o in objs:
            if o.__class__ is HdlVariableDef and o.type == HdlTypeType:
                types.append(o)
            else:
                obj_type_containers[o.__class__].append(o)

        return types, variables, processes, components

    def visit_HdlModuleDef(self, mod_def):
        """
        :type mod_def: HdlModuleDef
        """
        mod_dec = mod_def.dec
        assert mod_dec is not None
        w = self.out.write
        if self.add_imports:
            w(DEFAULT_IMPORTS)
            w("\n")
            if self.module_path_prefix is None:
                self.add_imports = False

        types, variables, processes, components = self.split_HdlModuleDefObjs(mod_def.objs)

        self.visit_component_imports(components)
        self.visit_doc(mod_dec)
        w("class ")
        w(mod_dec.name)
        w("(BasicRtlSimModel):\n")
        assert not mod_dec.params, "generic should be already resolved for this format"

        with Indent(self.out):
            for t in types:
                self.visit_type_declr(t)

            w('def __init__(self, sim: "BasicRtlSimulator", name="')
            w(mod_dec.name)
            w('"):\n')
            with Indent(self.out):
                w('BasicRtlSimModel.__init__(self, sim, name=name)\n')
                w('# ports\n')
                for port in mod_dec.ports:
                    self.visit_HdlVariableDef(port)
                w("# internal signals\n")
                for v in variables:
                    self.visit_HdlVariableDef(v)
                w("# component instances\n")
                for c in components:
                    w("self.")
                    w(c.name.val)
                    w(" = ")
                    w(c.module_name.val)
                    w('(sim, "')
                    w(c.name.val)
                    w('")\n')

            w("def _init_body(self):\n")
            with Indent(self.out):
                for c in components:
                    for pm in c.port_map:
                        w("connectSimPort(self, self.")
                        w(c.name.val)
                        w(', "')
                        assert isinstance(pm, HdlCall) and\
                            pm.fn == HdlBuiltinFn.MAP_ASSOCIATION, pm
                        mod_port, connected_sig = pm.ops
                        assert isinstance(
                            connected_sig, HdlName), connected_sig
                        self.visit_iHdlExpr(connected_sig)
                        w('", "')
                        assert isinstance(mod_port, HdlName), mod_port
                        self.visit_iHdlExpr(mod_port)
                        w('")\n')

                w('self._interfaces = (\n')
                with Indent(self.out):
                    for p in chain(mod_dec.ports, variables):
                        if not p.is_const:
                            w("self.io.")
                            w(p.name)
                            w(',\n')
                w(')\n')

                w('self._processes = (\n')
                with Indent(self.out):
                    for p in processes:
                        w("self.")
                        try:
                            w(p.labels[0])
                        except Exception:
                            raise
                        w(",\n")
                w(")\n")
                w('self._units = (')
                with Indent(self.out):
                    for c in components:
                        w("self.")
                        w(c.name.val)
                        w(",\n")
                w(")\n")

                for proc in processes:
                    w("sensitivity(self.")
                    w(proc.labels[0])
                    w(', ')
                    for last, s in iter_with_last(proc.sensitivity):
                        if isinstance(s, HdlCall):
                            w("(")
                            w(str(sensitivityByOp(s.fn)))
                            w(", self.io.")
                            self.visit_iHdlExpr(s.ops[0])
                            w(')')
                        else:
                            w("self.io.")
                            self.visit_iHdlExpr(s)
                        if not last:
                            w(", ")
                    w(")\n")
                    w("self._outputs[self.")
                    w(proc.labels[0])
                    w("] = (\n")
                    outputs = self.stm_outputs[proc]
                    with Indent(self.out):
                        for outp in outputs:
                            w("self.io.")
                            assert isinstance(outp, HdlName)
                            w(outp.val)
                            w(",\n")
                    w(")\n")
                w("for u in self._units:\n")
                w("    u._init_body()\n\n")
            for p in processes:
                self.visit_HdlStmProcess(p)
                # extra line to separate a process functions
                w("\n")

    def visit_type_declr(self, t):
        """
        :type t: HdlVariableDef
        """
        self.visit_doc(t)
        w = self.out.write
        w(t.name)
        w(" = ")
        val = t.value
        if isinstance(val, HdlEnumDef):
            w('define_Enum3t("')
            w(t.name)
            w('", [')
            for last, (k, v) in iter_with_last(val.values):
                assert v is None, v
                w('"%s"' % k)
                if not last:
                    w(", ")
            w("])()\n")
        elif isinstance(val, HdlCall) and val.fn == HdlBuiltinFn.INDEX:
            # array type def.
            self.visit_iHdlExpr(val)
            w("\n")
        else:
            raise NotImplementedError()

    def visit_HdlVariableDef(self, var):
        """
        :type var: HdlVariableDef
        """
        self.visit_doc(var)
        w = self.out.write
        if var.is_const:
            w("self.")
            w(var.name)
            w(" = ")
            self.visit_iHdlExpr(var.value)
            w("\n")
        else:
            w("self.io.")
            w(var.name)
            w(' = BasicRtlSimProxy(\n')
            with Indent(self.out):
                w('sim, self, "')
                w(var.name)
                w('",\n')
                self.visit_type(var.type)
                w(", ")
            if var.value is None:
                w("None)\n")
            else:
                self.visit_iHdlExpr(var.value)
                w(")\n")

    def visit_HdlContext(self, context, stm_outputs):
        """
        :type context: HdlContext
        :type stm_outputs: Dict[HdlStm, List[HdlName]]
        """
        self.stm_outputs = stm_outputs
        return super(ToBasicHdlSimModel, self).visit_HdlContext(context)


if __name__ == "__main__":
    import os
    import sys
    from hdlConvertor.language import Language
    from hdlConvertor import HdlConvertor
    from hdlConvertor.translate.verilog_to_basic_hdl_sim_model import\
        verilog_to_basic_hdl_sim_model

    BASE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..")
    TEST_DIR = os.path.join(BASE_DIR, 'tests', 'verilog')

    c = HdlConvertor()
    filenames = [os.path.join(TEST_DIR, "arbiter.v")]
    d = c.parse(filenames, Language.VERILOG, [], False, True)
    d, stm_outputs, ns = verilog_to_basic_hdl_sim_model(d)
    tv = ToBasicHdlSimModel(sys.stdout)
    tv.visit_HdlContext(d, stm_outputs)
