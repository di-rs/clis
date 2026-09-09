use std::iter;

use proc_macro2::TokenStream;
use quote::{ToTokens, quote};
use syn::{
    parse::{Parse, ParseStream},
    parse_macro_input,
};

// comp: mapping for_if_clause+
//
// mapping: expression
//
// for_if_clause:
//    | 'for' pattern 'in' expression ('if' expression)*
//
// pattern: name (, name)*

// Nested arrays for_if_clause
//
// let vec_of_vecs = vec![vec![1, 2, 3], vec![4, 5, 6]];
//
// x for vec in vec_of_vecs for x in vec
//

struct Comp {
    mapping: Mapping,
    for_if_clause: ForIfClause,
    additional_for_if_clauses: Vec<ForIfClause>,
}

impl Parse for Comp {
    fn parse(input: ParseStream) -> syn::Result<Self> {
        Ok(Self {
            mapping: input.parse()?,
            for_if_clause: input.parse()?,
            additional_for_if_clauses: parse_zero_or_more(input),
        })
    }
}

impl quote::ToTokens for Comp {
    fn to_tokens(&self, tokens: &mut TokenStream) {
        let all_for_if_clauses =
            iter::once(&self.for_if_clause).chain(&self.additional_for_if_clauses);
        let mut innermost_to_outermost = all_for_if_clauses.rev();

        let mut output = {
            let innermost = innermost_to_outermost
                .next()
                .expect("At least one will be available");

            let ForIfClause {
                pattern,
                sequence,
                conditions,
            } = innermost;

            let Mapping(mapping) = &self.mapping;

            quote! {
                core::iter::IntoIterator::into_iter(#sequence).filter_map(|#pattern| {
                    (true #(&& (#conditions))*).then(|| #mapping)
                })
            }
        };

        output = innermost_to_outermost.fold(output, |current_output, next_layer| {
            let ForIfClause {
                pattern,
                sequence,
                conditions,
            } = next_layer;
            quote! {
                core::iter::IntoIterator::into_iter(#sequence).filter_map(|#pattern| {
                    (true #(&& (#conditions))*).then(|| #current_output)
                })
                .flatten()
            }
        });

        tokens.extend(output);
    }
}

struct Mapping(syn::Expr);

impl Parse for Mapping {
    fn parse(input: ParseStream) -> syn::Result<Self> {
        input.parse().map(Self)
    }
}

impl ToTokens for Mapping {
    fn to_tokens(&self, tokens: &mut TokenStream) {
        self.0.to_tokens(tokens);
    }
}

struct ForIfClause {
    pattern: Pattern,
    sequence: syn::Expr,
    conditions: Vec<Condition>,
}

// for x in xs if y if z if w

impl Parse for ForIfClause {
    fn parse(input: ParseStream) -> syn::Result<Self> {
        input.parse::<syn::Token![for]>()?;
        let pattern = input.parse()?;
        input.parse::<syn::Token![in]>()?;
        let sequence = input.parse()?;
        let conditions = parse_zero_or_more(input);
        Ok(Self {
            pattern,
            sequence,
            conditions,
        })
    }
}

fn parse_zero_or_more<T: Parse>(input: ParseStream) -> Vec<T> {
    let mut result = Vec::new();
    while let Ok(item) = input.parse() {
        result.push(item);
    }
    result
}

struct Pattern(syn::Pat);

// Parse "stream"
// for | x | in | xs | [ | 1 | , | 2 | ]

impl Parse for Pattern {
    fn parse(input: ParseStream) -> syn::Result<Self> {
        input.call(syn::Pat::parse_single).map(Self)
    }
}

impl ToTokens for Pattern {
    fn to_tokens(&self, tokens: &mut TokenStream) {
        self.0.to_tokens(tokens);
    }
}

struct Condition(syn::Expr);

impl Parse for Condition {
    fn parse(input: ParseStream) -> syn::Result<Self> {
        // parse if
        input.parse::<syn::Token![if]>()?;
        input.parse().map(Self)
    }
}

impl ToTokens for Condition {
    fn to_tokens(&self, tokens: &mut TokenStream) {
        self.0.to_tokens(tokens)
    }
}

#[proc_macro]
pub fn comp(input: proc_macro::TokenStream) -> proc_macro::TokenStream {
    let c = parse_macro_input!(input as Comp);
    quote! { #c }.into()
}
