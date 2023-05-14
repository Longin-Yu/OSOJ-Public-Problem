from sys import argv
if argv[1].strip() == argv[2].strip(): 
  exit(0)
print("Not Matched.")
exit(1)