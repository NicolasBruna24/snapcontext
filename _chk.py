from tree_sitter_language_pack import get_parser
p = get_parser('rust')
t = p.parse(bytes('fn main(){}','utf8'))
print('rust ok', t.root_node.type)
