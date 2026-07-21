EPL Inventory Checker v0.5.2
============================

True stockout highlighting
--------------------------

A result row is highlighted light red when:

- Piqua Available Physical is zero or negative, and
- no Hobart branch or service contractor has positive Available Physical stock.

The Alternate Stock column displays "OUT OF STOCK".

Parts that have alternate inventory still show the View Locations button
and are not highlighted red.

Install
-------

Replace main.py in the existing project folder, then run:

    python main.py
