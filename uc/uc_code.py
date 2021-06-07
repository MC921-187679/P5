from __future__ import annotations
import argparse
import pathlib
import sys
from typing import Any, Literal, Optional, TextIO, Tuple, Type, Union, overload
from uc.uc_ast import (
    ID,
    AddressOp,
    ArrayDecl,
    ArrayRef,
    Assignment,
    BinaryOp,
    BoolConstant,
    CharConstant,
    Constant,
    Decl,
    FloatConstant,
    FuncCall,
    FuncDecl,
    FuncDef,
    IntConstant,
    Node,
    ParamList,
    Print,
    Program,
    RelationOp,
    StringConstant,
    UnaryOp,
    VarDecl,
)
from uc.uc_block import (
    CFG,
    BasicBlock,
    EmitBlocks,
    FunctionBlock,
    GlobalBlock,
    Variable,
)
from uc.uc_interpreter import Interpreter
from uc.uc_ir import (
    AddInstr,
    AllocInstr,
    AndInstr,
    BinaryOpInstruction,
    CallInstr,
    CopyInstr,
    DivInstr,
    ElemInstr,
    EqInstr,
    GeInstr,
    GetInstr,
    GlobalVariable,
    GtInstr,
    Instruction,
    LeInstr,
    LiteralInstr,
    LoadInstr,
    LtInstr,
    ModInstr,
    MulInstr,
    NamedVariable,
    NeInstr,
    NotInstr,
    OrInstr,
    ParamInstr,
    PrintInstr,
    StoreInstr,
    SubInstr,
    TempVariable,
    TextVariable,
    UnaryOpInstruction,
)
from uc.uc_parser import UCParser
from uc.uc_sema import NodeVisitor, Visitor
from uc.uc_type import IntType, PrimaryType, uCType

# instructions for basic operations
binary_op: dict[str, Type[BinaryOpInstruction]] = {
    "+": AddInstr,
    "-": SubInstr,
    "*": MulInstr,
    "/": DivInstr,
    "%": ModInstr,
    "<": LtInstr,
    "<=": LeInstr,
    ">": GtInstr,
    ">=": GeInstr,
    "==": EqInstr,
    "!=": NeInstr,
    "&&": AndInstr,
    "||": OrInstr,
}
unary_op: dict[str, Type[UnaryOpInstruction]] = {"!": NotInstr}


class CodeGenerator(NodeVisitor[Optional[TempVariable]]):
    """
    Node visitor class that creates 3-address encoded instruction sequences
    with Basic Blocks & Control Flow Graph.
    """

    def __init__(self, viewcfg: bool):
        self.viewcfg = viewcfg

        self.glob = GlobalBlock()
        self.current: Optional[BasicBlock] = None

        # TODO: Complete if needed.

    def show(self, buf: TextIO = sys.stdout) -> None:
        text = ""
        for code in self.code:
            text += code.format() + "\n"
        buf.write(text)

    @property
    def code(self) -> list[Instruction]:
        """
        The generated code (can be mapped to a list of tuples)
        """
        return EmitBlocks().visit(self.glob)

    # You must implement visit_Nodename methods for all of the other
    # AST nodes.  In your code, you will need to make instructions
    # and append them to the current block code list.
    #
    # A few sample methods follow. Do not hesitate to complete or change
    # them if needed.

    # # # # # # # # #
    # DECLARATIONS  #

    def visit_Program(self, node: Program) -> None:
        # Visit all of the global declarations
        for decl in node.gdecls:
            self.visit(decl)

        if self.viewcfg:  # evaluate to True if -cfg flag is present in command line
            dot = CFG(node.name)
            dot.view(node)

    def visit_Decl(self, node: Decl) -> None:
        self.visit(node.type, node.init)

    def visit_ArrayDecl(self, node: ArrayDecl, init: Optional[Node]) -> None:
        # space for pointer / reference
        varname = self._varname(node.declname)
        self.current.append(AllocInstr(IntType, varname))
        # space for data
        data = NamedVariable(f".{varname.value}.content")
        self.current.append(AllocInstr(node.uc_type, data))
        # store pointer in variable
        pointer = self.current.new_temp()
        self.current.append(GetInstr(node.uc_type, data, pointer))
        self.current.append(StoreInstr(IntType, pointer, varname))
        # copy initialization data
        if init is not None:
            value = self.visit(init)
            self.current.append(CopyInstr(node.uc_type, value, pointer))

    def visit_VarDecl(self, node: VarDecl, init: Optional[Node]) -> None:
        varname = self._varname(node.declname)
        alloc = AllocInstr(node.uc_type, varname)
        self.current.append(alloc)

        if init is not None:
            value = self.visit(init)
            store = StoreInstr(node.uc_type, value, varname)
            self.current.append(store)

    def visit_FuncDef(self, node: FuncDef) -> None:
        decl = node.declaration.type
        # create function block
        block = FunctionBlock(self.glob, decl.uc_type)
        # create entry block and populate it
        self.current = block.entry
        self.visit(decl.param_list)
        self.visit(node.decl_list)
        # visit body
        self.visit(node.implementation)
        # remove from block list
        self.current = None

    def visit_ParamList(self, node: ParamList) -> None:
        for decl, (_, _, tempvar) in zip(node.params, self.current.function.params):
            varname = self._varname(decl.name)
            alloc = AllocInstr(decl.type.uc_type, varname)
            store = StoreInstr(decl.type.uc_type, tempvar, varname)

            self.current.append(alloc)
            self.current.append(store)

    # # # # # # # #
    # STATEMENTS  #

    def visit_Print(self, node: Print) -> None:
        # empty
        if node.param is None:
            instr = PrintInstr()
            self.current.append(instr)
            return

        for param in node.param.expr:
            value = self.visit(param)
            instr = PrintInstr(param.uc_type, value)
            self.current.append(instr)

    # # # # # # # #
    # EXPRESSIONS #

    def visit_Assignment(self, node: Assignment) -> TempVariable:
        value = self.visit(node.left)
        if isinstance(node.right, ID):
            target = self._varname(node.right)
            instr = StoreInstr(node.uc_type, value, target)
        else:
            target = self.visit(node.right, ref=True)
            zero = self._new_constant(IntType, 0)
            instr = ElemInstr(node.uc_type, value, zero, target)
        self.current.append(instr)
        return value

    def visit_BinaryOp(self, node: BinaryOp) -> TempVariable:
        # Visit the left and right expressions
        left = self.visit(node.left)
        right = self.visit(node.right)
        # Make a new temporary for storing the result
        target = self.current.new_temp()

        # Create the opcode and append to list
        instr = binary_op[node.op](node.uc_type, left, right, target)
        self.current.append(instr)
        return target

    def visit_RelationOp(self, node: RelationOp) -> TempVariable:
        return self.visit_BinaryOp(node)

    def visit_UnaryOp(self, node: UnaryOp) -> TempVariable:
        # get source and target registers
        source = self.visit(node.expr)
        target = self.current.new_temp()

        # Create the opcode and append to list
        instr = unary_op[node.op](node.uc_type, source, target)
        self.current.append(instr)
        return target

    def visit_AddressOp(self, node: AddressOp, ref: bool = False) -> TempVariable:
        source = self.visit(node.expr, ref=True)
        # get address
        if node.op == "&" or ref:
            return source
        # or element
        else:
            index = self._new_constant(IntType, 0)
            target = self.current.new_temp()
            instr = ElemInstr(node.uc_type, source, index, target)
            self.current.append(instr)
            return target

    def visit_ArrayRef(self, node: ArrayRef, ref: bool = False) -> TempVariable:
        source = self.visit(node.array)
        index = self.visit(node.index)
        # calculate offset, if needed
        if node.uc_type.sizeof() != 1:
            offset = self.current.new_temp()
            size = self._new_constant(IntType, node.uc_type.sizeof())
            instr = MulInstr(IntType, size, index, offset)
            self.current.append(instr)
        else:
            offset = index
        # return reference for compound types
        if ref or not isinstance(node.uc_type, PrimaryType):
            address = self.current.new_temp()
            instr = AddInstr(node.uc_type, source, offset, address)
            self.current.append(instr)
            return address
        # and value for primaries
        else:
            value = self.current.new_temp()
            instr = ElemInstr(node.uc_type, source, offset, value)
            self.current.append(instr)
            return value

    def visit_FuncCall(self, node: FuncCall) -> TempVariable:
        # get function address
        source = self.visit(node.callable)
        # load parameters
        for param in node.parameters():
            varname = self.visit(param)
            instr = ParamInstr(param.uc_type, varname)
            self.current.append(instr)
        # then call function
        target = self.current.new_temp()
        isntr = CallInstr(node.uc_type, source, target)
        self.current.append(instr)
        return target

    # # # # # # # # #
    # BASIC SYMBOLS #

    def _new_constant(self, uctype: PrimaryType, value: Any) -> TempVariable:
        # Create a new temporary variable name
        target = self.current.new_temp()
        # Make the SSA opcode and append to list of generated instructions
        instr = LiteralInstr(uctype, value, target)
        self.current.append(instr)
        return target

    def visit_Constant(self, node: Constant) -> TempVariable:
        return self._new_constant(node.uc_type, node.value)

    def visit_IntConstant(self, node: IntConstant) -> TempVariable:
        return self.visit_Constant(node)

    def visit_FloatConstant(self, node: FloatConstant) -> TempVariable:
        return self.visit_Constant(node)

    def visit_BoolConstant(self, node: BoolConstant) -> TempVariable:
        return self.visit_Constant(node)

    def visit_CharConstant(self, node: CharConstant) -> TempVariable:
        return self.visit_Constant(node)

    def visit_StringConstant(self, node: StringConstant) -> TextVariable:  # TODO
        return self.glob.new_literal(node.uc_type, node.value)

    def _varname(self, ident: ID) -> NamedVariable:
        """Get variable name for identifier"""
        if ident.is_global:
            return GlobalVariable(ident.name)
        else:
            return NamedVariable(ident.name)

    def visit_ID(self, node: ID, ref: bool = False) -> TempVariable:
        stackvar = self._varname(node)
        register = self.current.new_temp()
        # load value into a register
        if not ref:
            instr = LoadInstr(node.uc_type, stackvar, register)
        # or load address
        else:
            instr = GetInstr(node.uc_type, stackvar, register)
        self.current.append(instr)
        return register


if __name__ == "__main__":

    # create argument parser
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "input_file",
        help="Path to file to be used to generate uCIR. By default, this script only runs the interpreter on the uCIR. \
              Use the other options for printing the uCIR, generating the CFG or for the debug mode.",
        type=str,
    )
    parser.add_argument(
        "--ir",
        help="Print uCIR generated from input_file.",
        action="store_true",
    )
    parser.add_argument("--cfg", help="Show the cfg of the input_file.", action="store_true")
    parser.add_argument("--debug", help="Run interpreter in debug mode.", action="store_true")
    args = parser.parse_args()

    print_ir: bool = args.ir
    create_cfg: bool = args.cfg
    interpreter_debug: bool = args.debug

    # get input path
    input_path = pathlib.Path(args.input_file)

    # check if file exists
    if not input_path.exists():
        print("Input", input_path, "not found", file=sys.stderr)
        sys.exit(1)

    # set error function
    p = UCParser()
    # open file and parse it
    with open(input_path) as f:
        ast = p.parse(f)

    sema = Visitor()
    sema.visit(ast)

    gen = CodeGenerator(create_cfg)
    gen.visit(ast)
    gencode = gen.code

    if print_ir:
        print("Generated uCIR: --------")
        gen.show()
        print("------------------------\n")

    vm = Interpreter(interpreter_debug)
    vm.run(gencode)
